from datetime import time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User
from organizations.models import Organization

from .models import (
    EmployeeProfile,
    ShiftRequirement,
    ShiftType,
    Skill,
    SkillRequirement,
    Weekday,
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