from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from organizations.models import Organization

from .models import User
from .phone import normalize_phone, validate_phone


class PhoneAuthenticationForm(AuthenticationForm):
    """Login form where the username is the mobile phone number."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Phone number")

    def clean_username(self):
        return normalize_phone(self.cleaned_data["username"])


class SignupForm(forms.Form):
    organization_name = forms.CharField(label=_("Business name"), max_length=150)
    first_name = forms.CharField(label=_("First name"), max_length=150)
    last_name = forms.CharField(label=_("Last name"), max_length=150)
    phone = forms.CharField(label=_("Phone number"), max_length=20)
    password1 = forms.CharField(label=_("Password"), widget=forms.PasswordInput)
    password2 = forms.CharField(label=_("Password confirmation"), widget=forms.PasswordInput)

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        validate_phone(phone)
        if User.objects.filter(phone=phone).exists():
            raise ValidationError(_("An account with this phone number already exists."))
        return phone

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", _("The two passwords do not match."))
            else:
                try:
                    validate_password(p1)
                except ValidationError as error:
                    self.add_error("password1", error)
        return cleaned

    @transaction.atomic
    def save(self):
        data = self.cleaned_data
        organization = Organization.objects.create(name=data["organization_name"])
        return User.objects.create_user(
            phone=data["phone"],
            password=data["password1"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            organization=organization,
            role=User.Role.OWNER,
        )