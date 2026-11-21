from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .models import (
    Availability,
    EmployeeProfile,
    LeaveRequest,
    Schedule,
    ShiftAssignment,
    ShiftRequirement,
    ShiftType,
    UnfilledSlot,
    Holiday,
)
from .solver import (
    DEFAULT_MIN_REST_MINUTES,
    EmployeeInfo,
    Problem,
    Requirement,
    ShiftInfo,
    Window,
    can_work,
    solve,
    is_holiday,
)

HISTORY_DAYS = 90


def _heavy_history(schedule, holiday_dates):
    """Count recent night and holiday shifts per employee.

    Only published schedules count, and only the HISTORY_DAYS days before
    this schedule starts.
    """
    since = schedule.start_date - timedelta(days=HISTORY_DAYS)
    past = (
        ShiftAssignment.objects.for_organization(schedule.organization)
        .filter(
            schedule__status=Schedule.Status.PUBLISHED,
            date__gte=since,
            date__lt=schedule.start_date,
        )
        .exclude(schedule=schedule)
        .select_related("shift_type")
    )
    nights = defaultdict(int)
    holidays = defaultdict(int)
    for item in past:
        if item.shift_type.is_night:
            nights[item.employee_id] += 1
        if is_holiday(item.date, holiday_dates):
            holidays[item.employee_id] += 1
    return nights, holidays


def build_problem(
    schedule,
    *,
    min_rest_minutes=DEFAULT_MIN_REST_MINUTES,
    time_limit_seconds=10.0,
):
    """Translate the organization's data into a solver Problem."""
    organization = schedule.organization

    profiles = list(
        EmployeeProfile.objects.for_organization(organization)
        .filter(is_active=True)
        .select_related("user")
        .prefetch_related("skills")
    )

    since = schedule.start_date - timedelta(days=HISTORY_DAYS)
    holiday_dates = set(
        Holiday.objects.for_organization(organization)
        .filter(date__gte=since, date__lte=schedule.end_date)
        .values_list("date", flat=True)
    )
    past_nights, past_holidays = _heavy_history(schedule, holiday_dates)

    employees = [
        EmployeeInfo(
            id=profile.id,
            name=str(profile.user),
            skill_ids=frozenset(skill.id for skill in profile.skills.all()),
            max_weekly_minutes=profile.max_weekly_hours * 60,
            past_night_shifts=past_nights[profile.id],
            past_holiday_shifts=past_holidays[profile.id],
        )
        for profile in profiles
    ]

    shifts = [
        ShiftInfo(
            id=s.id,
            name=s.name,
            start_time=s.start_time,
            end_time=s.end_time,
            is_night=s.is_night,
        )
        for s in ShiftType.objects.for_organization(organization)
    ]
    shifts_by_id = {s.id: s for s in shifts}

    rows_by_weekday = defaultdict(list)
    rows = ShiftRequirement.objects.for_organization(organization).prefetch_related(
        "skill_requirements"
    )
    for row in rows:
        rows_by_weekday[row.weekday].append(row)

    requirements = []
    for day in schedule.dates():
        for row in rows_by_weekday[day.weekday()]:
            requirements.append(
                Requirement(
                    day=day,
                    shift_id=row.shift_type_id,
                    required_count=row.required_count,
                    skill_minimums={
                        item.skill_id: item.min_count
                        for item in row.skill_requirements.all()
                    },
                )
            )

    # Days covered by approved leave, per employee.
    leave_days = defaultdict(set)
    leaves = (
        LeaveRequest.objects.for_organization(organization)
        .approved()
        .filter(start_date__lte=schedule.end_date, end_date__gte=schedule.start_date)
    )
    for leave in leaves:
        day = max(leave.start_date, schedule.start_date)
        last = min(leave.end_date, schedule.end_date)
        while day <= last:
            leave_days[leave.employee_id].add(day)
            day += timedelta(days=1)

    windows = defaultdict(list)
    for item in Availability.objects.for_organization(organization):
        windows[item.employee_id].append(
            Window(
                weekday=item.weekday,
                start_time=item.start_time,
                end_time=item.end_time,
                available=item.kind == Availability.Kind.AVAILABLE,
            )
        )

    unavailable = set()
    for req in requirements:
        shift = shifts_by_id[req.shift_id]
        for employee in employees:
            if req.day in leave_days[employee.id] or not can_work(
                windows[employee.id], req.day, shift
            ):
                unavailable.add((employee.id, req.day, req.shift_id))

    return Problem(
        employees=employees,
        shifts=shifts,
        requirements=requirements,
        unavailable=unavailable,
        holidays=holiday_dates,
        min_rest_minutes=min_rest_minutes,
        time_limit_seconds=time_limit_seconds,
    )


def save_solution(schedule, solution):
    """Replace the schedule's assignments and unfilled slots with the solution."""
    with transaction.atomic():
        schedule.assignments.all().delete()
        schedule.unfilled_slots.all().delete()
        ShiftAssignment.objects.bulk_create(
            [
                ShiftAssignment(
                    organization_id=schedule.organization_id,
                    schedule=schedule,
                    employee_id=a.employee_id,
                    shift_type_id=a.shift_id,
                    date=a.day,
                )
                for a in solution.assignments
            ]
        )
        UnfilledSlot.objects.bulk_create(
            [
                UnfilledSlot(
                    organization_id=schedule.organization_id,
                    schedule=schedule,
                    date=s.day,
                    shift_type_id=s.shift_id,
                    skill_id=s.skill_id,
                    missing_count=s.missing_count,
                    reason=s.reason,
                )
                for s in solution.shortages
            ]
        )


def generate_schedule(
    schedule,
    *,
    min_rest_minutes=DEFAULT_MIN_REST_MINUTES,
    time_limit_seconds=10.0,
):
    """Generate (or regenerate) a draft schedule and store the result."""
    if schedule.status != Schedule.Status.DRAFT:
        raise ValidationError(_("Only draft schedules can be generated."))
    problem = build_problem(
        schedule,
        min_rest_minutes=min_rest_minutes,
        time_limit_seconds=time_limit_seconds,
    )
    # Solve outside the transaction so the database is not locked meanwhile.
    # If the solver fails, nothing is deleted.
    solution = solve(problem)
    save_solution(schedule, solution)
    return solution