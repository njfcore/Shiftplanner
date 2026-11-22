from datetime import date, time

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import User
from organizations.models import Organization

from .models import (
    Availability,
    EmployeeProfile,
    LeaveRequest,
    Schedule,
    ShiftRequirement,
    ShiftType,
    Skill,
    SkillRequirement,
    Weekday,
    Holiday, 
    ShiftAssignment,
)
from .services import generate_schedule, check_schedule

SAT = date(2026, 11, 14)
SUN = date(2026, 11, 15)


class GeneratorTestCase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.morning = ShiftType.objects.create(
            organization=self.org, name="Morning",
            start_time=time(8, 0), end_time=time(16, 0),
        )
        self.schedule = Schedule.objects.create(
            organization=self.org, start_date=SAT, end_date=SUN
        )
        self._phone_counter = 0

    def employee(self, org=None, **kwargs):
        org = org or self.org
        self._phone_counter += 1
        user = User.objects.create_user(
            phone=f"0912000{self._phone_counter:04d}",
            password="Str0ng-pass-123",
            organization=org,
        )
        return EmployeeProfile.objects.create(organization=org, user=user, **kwargs)

    def require(self, weekday, count, skills=None):
        requirement = ShiftRequirement.objects.create(
            organization=self.org, shift_type=self.morning,
            weekday=weekday, required_count=count,
        )
        for skill, minimum in (skills or {}).items():
            SkillRequirement.objects.create(
                requirement=requirement, skill=skill, min_count=minimum
            )
        return requirement

    def assigned_ids(self):
        return {a.employee_id for a in self.schedule.assignments.all()}


class GenerateScheduleTests(GeneratorTestCase):
    def test_generates_assignments_without_shortage(self):
        self.employee()
        self.employee()
        self.require(Weekday.SATURDAY, 2)
        solution = generate_schedule(self.schedule)
        self.assertTrue(solution.is_complete)
        self.assertEqual(self.schedule.assignments.count(), 2)
        self.assertEqual(self.schedule.unfilled_slots.count(), 0)
        self.assertEqual({a.date for a in self.schedule.assignments.all()}, {SAT})

    def test_shortage_is_saved_as_unfilled_slot(self):
        self.employee()
        self.require(Weekday.SATURDAY, 2)
        generate_schedule(self.schedule)
        self.assertEqual(self.schedule.assignments.count(), 1)
        slot = self.schedule.unfilled_slots.get()
        self.assertEqual(slot.date, SAT)
        self.assertEqual(slot.shift_type, self.morning)
        self.assertIsNone(slot.skill)
        self.assertEqual(slot.missing_count, 1)
        self.assertTrue(slot.reason)

    def test_regeneration_replaces_previous_result(self):
        self.employee()
        self.employee()
        self.require(Weekday.SATURDAY, 2)
        generate_schedule(self.schedule)
        generate_schedule(self.schedule)
        self.assertEqual(self.schedule.assignments.count(), 2)

    def test_published_schedule_cannot_be_generated(self):
        self.schedule.status = Schedule.Status.PUBLISHED
        self.schedule.save()
        with self.assertRaises(ValidationError):
            generate_schedule(self.schedule)

    def test_inactive_employee_is_ignored(self):
        self.employee(is_active=False)
        active = self.employee()
        self.require(Weekday.SATURDAY, 1)
        generate_schedule(self.schedule)
        self.assertEqual(self.assigned_ids(), {active.id})

    def test_other_organizations_are_ignored(self):
        other = Organization.objects.create(name="Other")
        mine = self.employee()
        self.employee(org=other)
        self.employee(org=other)
        self.require(Weekday.SATURDAY, 2)
        generate_schedule(self.schedule)
        self.assertEqual(self.assigned_ids(), {mine.id})
        self.assertEqual(self.schedule.unfilled_slots.get().missing_count, 1)

    def test_weekly_hours_of_profile_are_applied(self):
        self.employee(max_weekly_hours=8)
        self.require(Weekday.SATURDAY, 1)
        self.require(Weekday.SUNDAY, 1)
        generate_schedule(self.schedule)
        self.assertEqual(self.schedule.assignments.count(), 1)
        self.assertEqual(self.schedule.unfilled_slots.get().missing_count, 1)

    def test_skill_requirement_is_used(self):
        cashier = Skill.objects.create(organization=self.org, name="Cashier")
        skilled = self.employee()
        skilled.skills.add(cashier)
        self.employee()
        self.require(Weekday.SATURDAY, 1, skills={cashier: 1})
        solution = generate_schedule(self.schedule)
        self.assertTrue(solution.is_complete)
        self.assertEqual(self.assigned_ids(), {skilled.id})


class AvailabilityRulesTests(GeneratorTestCase):
    def test_approved_leave_blocks_but_pending_does_not(self):
        on_leave = self.employee()
        pending = self.employee()
        LeaveRequest.objects.create(
            organization=self.org, employee=on_leave, start_date=SAT, end_date=SAT,
            status=LeaveRequest.Status.APPROVED,
        )
        LeaveRequest.objects.create(
            organization=self.org, employee=pending, start_date=SAT, end_date=SAT,
        )
        self.require(Weekday.SATURDAY, 1)
        generate_schedule(self.schedule)
        self.assertEqual(self.assigned_ids(), {pending.id})

    def test_unavailable_window_blocks_employee(self):
        blocked = self.employee()
        free = self.employee()
        Availability.objects.create(
            organization=self.org, employee=blocked, weekday=Weekday.SATURDAY,
            start_time=time(6, 0), end_time=time(10, 0),
            kind=Availability.Kind.UNAVAILABLE,
        )
        self.require(Weekday.SATURDAY, 1)
        generate_schedule(self.schedule)
        self.assertEqual(self.assigned_ids(), {free.id})

    def test_available_window_restricts_employee(self):
        restricted = self.employee()
        free = self.employee()
        Availability.objects.create(
            organization=self.org, employee=restricted, weekday=Weekday.SATURDAY,
            start_time=time(9, 0), end_time=time(13, 0),
            kind=Availability.Kind.AVAILABLE,
        )
        self.require(Weekday.SATURDAY, 1)
        generate_schedule(self.schedule)
        self.assertEqual(self.assigned_ids(), {free.id})


class FairnessHistoryTests(GeneratorTestCase):
    def setUp(self):
        super().setUp()
        self.night = ShiftType.objects.create(
            organization=self.org, name="Night",
            start_time=time(22, 0), end_time=time(6, 0), is_night=True,
        )
        self.previous = Schedule.objects.create(
            organization=self.org, start_date=date(2026, 11, 7),
            end_date=date(2026, 11, 13), status=Schedule.Status.PUBLISHED,
        )
        self.a = self.employee()
        self.b = self.employee()

    def require_night(self, weekday):
        ShiftRequirement.objects.create(
            organization=self.org, shift_type=self.night,
            weekday=weekday, required_count=1,
        )

    def past_assignment(self, employee, day, shift):
        ShiftAssignment.objects.create(
            organization=self.org, schedule=self.previous,
            employee=employee, shift_type=shift, date=day,
        )

    def assigned_employee_ids(self):
        return list(self.schedule.assignments.values_list("employee_id", flat=True))

    def test_past_published_nights_are_balanced(self):
        self.past_assignment(self.a, date(2026, 11, 7), self.night)
        self.past_assignment(self.a, date(2026, 11, 8), self.night)
        self.require_night(Weekday.SATURDAY)
        self.require_night(Weekday.SUNDAY)
        generate_schedule(self.schedule)
        self.assertCountEqual(self.assigned_employee_ids(), [self.b.id, self.b.id])

    def test_draft_schedules_are_ignored_as_history(self):
        self.previous.status = Schedule.Status.DRAFT
        self.previous.save()
        self.past_assignment(self.a, date(2026, 11, 7), self.night)
        self.past_assignment(self.a, date(2026, 11, 8), self.night)
        self.require_night(Weekday.SATURDAY)
        self.require_night(Weekday.SUNDAY)
        generate_schedule(self.schedule)
        self.assertCountEqual(self.assigned_employee_ids(), [self.a.id, self.b.id])

    def test_registered_holiday_and_friday_history(self):
        Holiday.objects.create(organization=self.org, date=SAT, name="Test holiday")
        # 2026-11-13 is a Friday, so A already worked one holiday shift.
        self.past_assignment(self.a, date(2026, 11, 13), self.morning)
        self.require(Weekday.SATURDAY, 1)
        self.require(Weekday.SUNDAY, 1)
        generate_schedule(self.schedule)
        saturday = self.schedule.assignments.get(date=SAT)
        self.assertEqual(saturday.employee_id, self.b.id)        


class ManualAssignmentTests(GeneratorTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.employee()
        self.b = self.employee()

    def manual(self, employee, day=SAT):
        return ShiftAssignment.objects.create(
            organization=self.org, schedule=self.schedule, employee=employee,
            shift_type=self.morning, date=day, is_manual=True,
        )

    def leave(self, employee, day=SAT):
        LeaveRequest.objects.create(
            organization=self.org, employee=employee, start_date=day, end_date=day,
            status=LeaveRequest.Status.APPROVED,
        )

    def test_manual_assignment_is_kept_even_during_leave(self):
        self.leave(self.b)
        self.require(Weekday.SATURDAY, 1)
        kept = self.manual(self.b)
        generate_schedule(self.schedule)
        assignment = self.schedule.assignments.get()
        self.assertEqual(assignment.pk, kept.pk)
        self.assertTrue(assignment.is_manual)

    def test_manual_assignment_counts_toward_coverage(self):
        self.employee()  # a third employee
        self.require(Weekday.SATURDAY, 2)
        self.manual(self.b)
        generate_schedule(self.schedule)
        self.assertEqual(self.schedule.assignments.count(), 2)
        self.assertIn(self.b.id, self.assigned_ids())
        self.assertEqual(self.schedule.assignments.filter(is_manual=True).count(), 1)

    def test_manual_assignment_is_discarded_when_requested(self):
        self.leave(self.b)
        self.require(Weekday.SATURDAY, 1)
        self.manual(self.b)
        generate_schedule(self.schedule, keep_manual=False)
        assignment = self.schedule.assignments.get()
        self.assertEqual(assignment.employee_id, self.a.id)
        self.assertFalse(assignment.is_manual)

    def test_manual_assignment_of_inactive_employee_is_left_alone(self):
        inactive = self.employee(is_active=False)
        self.manual(inactive)
        self.require(Weekday.SATURDAY, 1)
        generate_schedule(self.schedule)
        self.assertEqual(self.schedule.assignments.filter(employee=inactive).count(), 1)
        self.assertEqual(self.schedule.assignments.filter(is_manual=False).count(), 1)


class CheckScheduleTests(GeneratorTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.employee()
        self.b = self.employee()

    def codes(self):
        return {v.code for v in check_schedule(self.schedule)}

    def test_generated_schedule_has_no_violations(self):
        self.require(Weekday.SATURDAY, 2)
        generate_schedule(self.schedule)
        self.assertEqual(check_schedule(self.schedule), [])

    def test_assignment_during_approved_leave_is_flagged(self):
        LeaveRequest.objects.create(
            organization=self.org, employee=self.a, start_date=SAT, end_date=SAT,
            status=LeaveRequest.Status.APPROVED,
        )
        ShiftAssignment.objects.create(
            organization=self.org, schedule=self.schedule, employee=self.a,
            shift_type=self.morning, date=SAT,
        )
        self.assertIn("unavailable", self.codes())

    def test_assignment_of_inactive_employee_is_flagged(self):
        inactive = self.employee(is_active=False)
        ShiftAssignment.objects.create(
            organization=self.org, schedule=self.schedule, employee=inactive,
            shift_type=self.morning, date=SAT,
        )
        self.assertIn("unknown_employee", self.codes())

    def test_removed_assignment_makes_schedule_understaffed(self):
        self.require(Weekday.SATURDAY, 2)
        generate_schedule(self.schedule)
        self.schedule.assignments.first().delete()
        self.assertIn("understaffed", self.codes())        