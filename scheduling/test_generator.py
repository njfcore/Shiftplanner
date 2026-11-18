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
)
from .services import generate_schedule

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