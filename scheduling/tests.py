from datetime import time, timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase

from organizations.models import Organization

from .models import ShiftType, Skill


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