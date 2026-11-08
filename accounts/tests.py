from django.test import TestCase
from django.urls import reverse

from organizations.models import Organization

from .models import User

PASSWORD = "Str0ng-pass-123"
PHONE = "09123456789"
PERSIAN_PHONE = "\u06f0\u06f9\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"


def signup_data(**overrides):
    data = {
        "organization_name": "Sample Cafe",
        "first_name": "Ali",
        "last_name": "Rezaei",
        "phone": PHONE,
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    data.update(overrides)
    return data


class SignupTests(TestCase):
    def test_signup_creates_organization_and_owner(self):
        response = self.client.post(reverse("accounts:signup"), signup_data())
        self.assertRedirects(response, reverse("dashboard"))
        user = User.objects.get(phone=PHONE)
        self.assertEqual(user.role, User.Role.OWNER)
        self.assertEqual(user.organization.name, "Sample Cafe")

    def test_signup_accepts_persian_digits(self):
        self.client.post(reverse("accounts:signup"), signup_data(phone=PERSIAN_PHONE))
        self.assertTrue(User.objects.filter(phone=PHONE).exists())

    def test_signup_rejects_duplicate_phone(self):
        org = Organization.objects.create(name="Other")
        User.objects.create_user(phone=PHONE, password=PASSWORD, organization=org)
        response = self.client.post(reverse("accounts:signup"), signup_data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Organization.objects.count(), 1)

    def test_signup_rejects_mismatched_passwords(self):
        response = self.client.post(
            reverse("accounts:signup"), signup_data(password2="different-pass-456")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Organization.objects.count(), 0)


class LoginAndTenantTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Sample Clinic")
        self.user = User.objects.create_user(
            phone=PHONE, password=PASSWORD, organization=self.org
        )

    def test_login_with_phone(self):
        response = self.client.post(
            reverse("accounts:login"), {"username": PERSIAN_PHONE, "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("dashboard"))

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_middleware_sets_request_organization(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.organization, self.org)

    def test_user_without_organization_is_forbidden(self):
        orphan = User.objects.create_user(phone="09120000000", password=PASSWORD)
        self.client.force_login(orphan)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_inactive_organization_is_forbidden(self):
        self.org.is_active = False
        self.org.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 403)