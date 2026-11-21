from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.test import TestCase

from accounts.models import User
from organizations.models import Organization

from .models import (
    Availability,
    EmployeeProfile,
    LeaveRequest,
    Schedule,
    ShiftAssignment,
    ShiftRequirement,
    ShiftType,
    Skill,
    SkillRequirement,
    UnfilledSlot,
    Weekday,
    Holiday
)


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Org A")
        self.org_b = Organization.objects.create(name="Org B")
        Skill.objects.create(organization=self.org_a, name="Cashier")
        Skill.objects.create(organization=self.org_b, name="Nurse")

    def test_for_organization_returns_only_own_rows(self):
        names = list(
            Skill.objects.for_organization(self.org_a).values_list("name", flat=True)
        )
        self.assertEqual(names, ["Cashier"])

    def test_for_organization_with_none_returns_nothing(self):
        self.assertEqual(Skill.objects.for_organization(None).count(), 0)

    def test_same_name_allowed_in_different_organizations(self):
        Skill.objects.create(organization=self.org_b, name="Cashier")
        self.assertEqual(Skill.objects.filter(name="Cashier").count(), 2)

    def test_duplicate_name_in_same_organization_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Skill.objects.create(organization=self.org_a, name="Cashier")


class ShiftTypeTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")

    def test_day_shift_duration(self):
        shift = ShiftType.objects.create(
            organization=self.org, name="Morning",
            start_time=time(8, 0), end_time=time(16, 0),
        )
        self.assertFalse(shift.crosses_midnight)
        self.assertEqual(shift.duration, timedelta(hours=8))

    def test_overnight_shift_crosses_midnight(self):
        shift = ShiftType.objects.create(
            organization=self.org, name="Night",
            start_time=time(22, 0), end_time=time(6, 0), is_night=True,
        )
        self.assertTrue(shift.crosses_midnight)
        self.assertEqual(shift.duration_hours, 8)

    def test_start_and_end_cannot_be_equal(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShiftType.objects.create(
                organization=self.org, name="Broken",
                start_time=time(8, 0), end_time=time(8, 0),
            )


class EmployeeProfileTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.other = Organization.objects.create(name="Other")
        self.user = User.objects.create_user(
            phone="09123456789", password="Str0ng-pass-123", organization=self.org
        )

    def test_defaults(self):
        profile = EmployeeProfile.objects.create(organization=self.org, user=self.user)
        self.assertEqual(profile.max_weekly_hours, 44)
        self.assertIsNone(profile.max_monthly_hours)
        self.assertTrue(profile.is_active)

    def test_skills_can_be_assigned(self):
        profile = EmployeeProfile.objects.create(organization=self.org, user=self.user)
        cashier = Skill.objects.create(organization=self.org, name="Cashier")
        profile.skills.add(cashier)
        self.assertEqual(list(cashier.employees.all()), [profile])

    def test_user_from_another_organization_is_invalid(self):
        profile = EmployeeProfile(organization=self.other, user=self.user)
        with self.assertRaises(ValidationError):
            profile.full_clean()


class ShiftRequirementTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.other = Organization.objects.create(name="Other")
        self.shift = ShiftType.objects.create(
            organization=self.org, name="Morning",
            start_time=time(8, 0), end_time=time(16, 0),
        )
        self.foreign_shift = ShiftType.objects.create(
            organization=self.other, name="Morning",
            start_time=time(8, 0), end_time=time(16, 0),
        )
        self.cashier = Skill.objects.create(organization=self.org, name="Cashier")
        self.foreign_skill = Skill.objects.create(organization=self.other, name="Nurse")
        self.requirement = ShiftRequirement.objects.create(
            organization=self.org, shift_type=self.shift,
            weekday=Weekday.MONDAY, required_count=2,
        )

    def test_one_requirement_per_shift_and_weekday(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShiftRequirement.objects.create(
                organization=self.org, shift_type=self.shift,
                weekday=Weekday.MONDAY, required_count=3,
            )

    def test_required_count_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShiftRequirement.objects.create(
                organization=self.org, shift_type=self.shift,
                weekday=Weekday.TUESDAY, required_count=0,
            )

    def test_shift_type_from_another_organization_is_invalid(self):
        requirement = ShiftRequirement(
            organization=self.org, shift_type=self.foreign_shift,
            weekday=Weekday.SATURDAY, required_count=1,
        )
        with self.assertRaises(ValidationError):
            requirement.full_clean()

    def test_valid_skill_requirement(self):
        item = SkillRequirement(requirement=self.requirement, skill=self.cashier, min_count=1)
        item.full_clean()

    def test_skill_min_count_cannot_exceed_required_staff(self):
        item = SkillRequirement(requirement=self.requirement, skill=self.cashier, min_count=3)
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_skill_from_another_organization_is_invalid(self):
        item = SkillRequirement(
            requirement=self.requirement, skill=self.foreign_skill, min_count=1
        )
        with self.assertRaises(ValidationError):
            item.full_clean()            


def make_employee(org, phone, role=User.Role.EMPLOYEE):
    user = User.objects.create_user(
        phone=phone, password="Str0ng-pass-123", organization=org, role=role
    )
    return EmployeeProfile.objects.create(organization=org, user=user)


class AvailabilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.other = Organization.objects.create(name="Other")
        self.employee = make_employee(self.org, "09120000001")
        self.foreign_employee = make_employee(self.other, "09120000002")

    def test_default_kind_is_available(self):
        window = Availability.objects.create(
            organization=self.org, employee=self.employee,
            weekday=Weekday.MONDAY, start_time=time(9, 0), end_time=time(13, 0),
        )
        self.assertEqual(window.kind, Availability.Kind.AVAILABLE)
        self.assertFalse(window.crosses_midnight)

    def test_overnight_window(self):
        window = Availability(
            organization=self.org, employee=self.employee,
            weekday=Weekday.FRIDAY, start_time=time(22, 0), end_time=time(6, 0),
            kind=Availability.Kind.UNAVAILABLE,
        )
        self.assertTrue(window.crosses_midnight)

    def test_start_and_end_cannot_be_equal(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Availability.objects.create(
                organization=self.org, employee=self.employee,
                weekday=Weekday.MONDAY, start_time=time(9, 0), end_time=time(9, 0),
            )

    def test_employee_from_another_organization_is_invalid(self):
        window = Availability(
            organization=self.org, employee=self.foreign_employee,
            weekday=Weekday.MONDAY, start_time=time(9, 0), end_time=time(13, 0),
        )
        with self.assertRaises(ValidationError):
            window.full_clean()


class LeaveRequestTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.other = Organization.objects.create(name="Other")
        self.employee = make_employee(self.org, "09120000001")
        self.manager = User.objects.create_user(
            phone="09120000003", password="Str0ng-pass-123",
            organization=self.org, role=User.Role.OWNER,
        )
        self.foreign_manager = User.objects.create_user(
            phone="09120000004", password="Str0ng-pass-123",
            organization=self.other, role=User.Role.OWNER,
        )

    def make_leave(self, start=date(2026, 11, 10), end=date(2026, 11, 12), **extra):
        return LeaveRequest.objects.create(
            organization=self.org, employee=self.employee,
            start_date=start, end_date=end, **extra,
        )

    def test_defaults_and_days(self):
        leave = self.make_leave()
        self.assertEqual(leave.status, LeaveRequest.Status.PENDING)
        self.assertEqual(leave.days, 3)

    def test_end_before_start_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_leave(start=date(2026, 11, 12), end=date(2026, 11, 10))

    def test_approve_records_reviewer_and_time(self):
        leave = self.make_leave()
        leave.approve(self.manager)
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(leave.reviewed_by, self.manager)
        self.assertIsNotNone(leave.reviewed_at)

    def test_reject(self):
        leave = self.make_leave()
        leave.reject(self.manager)
        self.assertEqual(leave.status, LeaveRequest.Status.REJECTED)

    def test_cannot_review_twice(self):
        leave = self.make_leave()
        leave.approve(self.manager)
        with self.assertRaises(ValidationError):
            leave.reject(self.manager)

    def test_reviewer_from_another_organization_is_rejected(self):
        leave = self.make_leave()
        with self.assertRaises(ValidationError):
            leave.approve(self.foreign_manager)
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveRequest.Status.PENDING)

    def test_employee_from_another_organization_is_invalid(self):
        foreign_employee = make_employee(self.other, "09120000005")
        leave = LeaveRequest(
            organization=self.org, employee=foreign_employee,
            start_date=date(2026, 11, 10), end_date=date(2026, 11, 11),
        )
        with self.assertRaises(ValidationError):
            leave.full_clean()

    def test_approved_and_covering_queryset(self):
        approved = self.make_leave()
        approved.approve(self.manager)
        self.make_leave(start=date(2026, 11, 11), end=date(2026, 11, 11))  # pending
        qs = LeaveRequest.objects.for_organization(self.org).approved().covering(date(2026, 11, 11))
        self.assertEqual(list(qs), [approved])
        self.assertEqual(
            LeaveRequest.objects.for_organization(self.org).approved().covering(date(2026, 11, 20)).count(),
            0,
        )            


class ScheduleTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.other = Organization.objects.create(name="Other")

    def test_days_and_dates(self):
        schedule = Schedule.objects.create(
            organization=self.org, start_date=date(2026, 11, 14), end_date=date(2026, 11, 20)
        )
        self.assertEqual(schedule.days, 7)
        self.assertEqual(schedule.dates()[0], date(2026, 11, 14))
        self.assertEqual(schedule.dates()[-1], date(2026, 11, 20))
        self.assertEqual(schedule.status, Schedule.Status.DRAFT)

    def test_end_before_start_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Schedule.objects.create(
                organization=self.org, start_date=date(2026, 11, 20), end_date=date(2026, 11, 14)
            )

    def test_creator_from_another_organization_is_invalid(self):
        foreign_user = User.objects.create_user(
            phone="09120000009", password="Str0ng-pass-123", organization=self.other
        )
        schedule = Schedule(
            organization=self.org, start_date=date(2026, 11, 14),
            end_date=date(2026, 11, 20), created_by=foreign_user,
        )
        with self.assertRaises(ValidationError):
            schedule.full_clean()


class ShiftAssignmentTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.other = Organization.objects.create(name="Other")
        self.employee = make_employee(self.org, "09120000001")
        self.foreign_employee = make_employee(self.other, "09120000002")
        self.morning = ShiftType.objects.create(
            organization=self.org, name="Morning",
            start_time=time(8, 0), end_time=time(16, 0),
        )
        self.night = ShiftType.objects.create(
            organization=self.org, name="Night",
            start_time=time(22, 0), end_time=time(6, 0), is_night=True,
        )
        self.schedule = Schedule.objects.create(
            organization=self.org, start_date=date(2026, 11, 14), end_date=date(2026, 11, 20)
        )

    def assign(self, day=date(2026, 11, 14), shift=None, employee=None):
        return ShiftAssignment.objects.create(
            organization=self.org, schedule=self.schedule,
            employee=employee or self.employee,
            shift_type=shift or self.morning, date=day,
        )

    def test_one_shift_per_employee_per_day(self):
        self.assign()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.assign(shift=self.night)

    def test_date_outside_schedule_is_invalid(self):
        item = ShiftAssignment(
            organization=self.org, schedule=self.schedule, employee=self.employee,
            shift_type=self.morning, date=date(2026, 11, 25),
        )
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_employee_from_another_organization_is_invalid(self):
        item = ShiftAssignment(
            organization=self.org, schedule=self.schedule, employee=self.foreign_employee,
            shift_type=self.morning, date=date(2026, 11, 14),
        )
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_overnight_assignment_times(self):
        item = self.assign(shift=self.night)
        self.assertEqual(item.start_at, datetime(2026, 11, 14, 22, 0))
        self.assertEqual(item.end_at, datetime(2026, 11, 15, 6, 0))
        self.assertEqual(item.hours, 8)

    def test_shift_type_with_assignments_cannot_be_deleted(self):
        self.assign()
        with self.assertRaises(RestrictedError):
            self.morning.delete()

    def test_deleting_organization_removes_everything(self):
        self.assign()
        self.org.delete()
        self.assertEqual(ShiftAssignment.objects.count(), 0)


class UnfilledSlotTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.other = Organization.objects.create(name="Other")
        self.shift = ShiftType.objects.create(
            organization=self.org, name="Night",
            start_time=time(22, 0), end_time=time(6, 0), is_night=True,
        )
        self.cashier = Skill.objects.create(organization=self.org, name="Cashier")
        self.foreign_skill = Skill.objects.create(organization=self.other, name="Nurse")
        self.schedule = Schedule.objects.create(
            organization=self.org, start_date=date(2026, 11, 14), end_date=date(2026, 11, 20)
        )

    def test_valid_unfilled_slot(self):
        slot = UnfilledSlot(
            organization=self.org, schedule=self.schedule, date=date(2026, 11, 16),
            shift_type=self.shift, skill=self.cashier, missing_count=1,
            reason="Everyone with this skill is on leave.",
        )
        slot.full_clean()
        slot.save()
        self.assertEqual(self.schedule.unfilled_slots.count(), 1)

    def test_missing_count_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnfilledSlot.objects.create(
                organization=self.org, schedule=self.schedule, date=date(2026, 11, 16),
                shift_type=self.shift, missing_count=0,
            )

    def test_skill_from_another_organization_is_invalid(self):
        slot = UnfilledSlot(
            organization=self.org, schedule=self.schedule, date=date(2026, 11, 16),
            shift_type=self.shift, skill=self.foreign_skill, missing_count=1,
        )
        with self.assertRaises(ValidationError):
            slot.full_clean()  


class HolidayTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.other = Organization.objects.create(name="Other")

    def test_same_date_allowed_in_different_organizations(self):
        Holiday.objects.create(organization=self.org, date=date(2026, 12, 21), name="Yalda")
        Holiday.objects.create(organization=self.other, date=date(2026, 12, 21))
        self.assertEqual(Holiday.objects.filter(date=date(2026, 12, 21)).count(), 2)

    def test_duplicate_date_in_same_organization_is_rejected(self):
        Holiday.objects.create(organization=self.org, date=date(2026, 12, 21))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Holiday.objects.create(organization=self.org, date=date(2026, 12, 21))            