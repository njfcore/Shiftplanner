from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .engine import week_start

SEVERITY_ERROR = "error"  # a hard rule is broken
SEVERITY_WARNING = "warning"  # coverage problem


@dataclass(frozen=True)
class Violation:
    code: str
    severity: str
    message: str
    day: date | None = None
    shift_id: int | None = None
    employee_id: int | None = None


def check_assignments(problem, assignments):
    """List every rule violation of a set of assignments (any origin).

    Errors: unknown or inactive employee, working while unavailable, two
    shifts on one day, weekly hour limit, minimum rest.
    Warnings: too few or too many people on a shift, missing skill, a shift
    that is not needed on that day.
    """
    shifts = {s.id: s for s in problem.shifts}
    employees = {e.id: e for e in problem.employees}
    violations = []

    def who(employee_id):
        return employees[employee_id].name or f"employee #{employee_id}"

    valid = []
    for a in assignments:
        if a.shift_id not in shifts:
            violations.append(
                Violation("unknown_shift", SEVERITY_ERROR, f"Unknown shift #{a.shift_id}.",
                          a.day, a.shift_id, a.employee_id)
            )
        elif a.employee_id not in employees:
            violations.append(
                Violation("unknown_employee", SEVERITY_ERROR,
                          "The assigned employee is inactive or unknown.",
                          a.day, a.shift_id, a.employee_id)
            )
        else:
            valid.append(a)

    for a in valid:
        if (a.employee_id, a.day, a.shift_id) in problem.unavailable:
            violations.append(
                Violation("unavailable", SEVERITY_ERROR,
                          f"{who(a.employee_id)} cannot work {shifts[a.shift_id].name} "
                          "on this day (leave or unavailable).",
                          a.day, a.shift_id, a.employee_id)
            )

    by_day = defaultdict(list)
    for a in valid:
        by_day[(a.employee_id, a.day)].append(a)
    for (employee_id, day), items in by_day.items():
        if len(items) > 1:
            names = ", ".join(sorted(shifts[i.shift_id].name for i in items))
            violations.append(
                Violation("double_booking", SEVERITY_ERROR,
                          f"{who(employee_id)} has more than one shift on the same day ({names}).",
                          day, None, employee_id)
            )

    per_week = defaultdict(int)
    for a in valid:
        per_week[(a.employee_id, week_start(a.day))] += shifts[a.shift_id].minutes
    for (employee_id, week), minutes in per_week.items():
        limit = employees[employee_id].max_weekly_minutes
        if minutes > limit:
            violations.append(
                Violation("weekly_limit", SEVERITY_ERROR,
                          f"{who(employee_id)} works {minutes / 60:g} hours in the week "
                          f"starting {week}, above the limit of {limit / 60:g}.",
                          week, None, employee_id)
            )

    def start_of(a):
        return datetime.combine(a.day, shifts[a.shift_id].start_time)

    def end_of(a):
        return start_of(a) + shifts[a.shift_id].duration

    rest = timedelta(minutes=problem.min_rest_minutes)
    by_employee = defaultdict(list)
    for a in valid:
        by_employee[a.employee_id].append(a)
    for employee_id, items in by_employee.items():
        items.sort(key=start_of)
        for i in range(len(items)):
            first_end = end_of(items[i])
            for j in range(i + 1, len(items)):
                second = items[j]
                if start_of(second) - first_end >= rest:
                    break
                if items[i].day == second.day:
                    continue  # reported as a double booking
                violations.append(
                    Violation("rest", SEVERITY_ERROR,
                              f"{who(employee_id)} has less than {problem.min_rest_minutes / 60:g} "
                              "hours of rest before this shift.",
                              second.day, second.shift_id, employee_id)
                )

    assigned_by_slot = defaultdict(list)
    for a in valid:
        assigned_by_slot[(a.day, a.shift_id)].append(a)

    required_slots = set()
    for req in problem.requirements:
        slot = (req.day, req.shift_id)
        required_slots.add(slot)
        assigned = assigned_by_slot.get(slot, [])
        name = shifts[req.shift_id].name
        if len(assigned) < req.required_count:
            violations.append(
                Violation("understaffed", SEVERITY_WARNING,
                          f"{name}: {len(assigned)} of {req.required_count} staff assigned.",
                          req.day, req.shift_id)
            )
        elif len(assigned) > req.required_count:
            violations.append(
                Violation("overstaffed", SEVERITY_WARNING,
                          f"{name}: {len(assigned)} staff assigned, only {req.required_count} needed.",
                          req.day, req.shift_id)
            )
        for skill_id, minimum in req.skill_minimums.items():
            have = sum(1 for a in assigned if skill_id in employees[a.employee_id].skill_ids)
            if have < minimum:
                violations.append(
                    Violation("missing_skill", SEVERITY_WARNING,
                              f"{name}: {have} of {minimum} required staff with skill #{skill_id}.",
                              req.day, req.shift_id)
                )

    for (day, shift_id) in assigned_by_slot:
        if (day, shift_id) not in required_slots:
            violations.append(
                Violation("unrequired_shift", SEVERITY_WARNING,
                          f"{shifts[shift_id].name} is not needed on this day.",
                          day, shift_id)
            )

    violations.sort(key=lambda v: (v.severity != SEVERITY_ERROR, v.day or date.min, v.code))
    return violations