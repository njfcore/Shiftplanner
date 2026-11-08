from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from .models import User


class AdminUserCreationForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("phone", "first_name", "last_name", "organization", "role")


class AdminUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    form = AdminUserChangeForm
    add_form = AdminUserCreationForm

    list_display = ("phone", "first_name", "last_name", "organization", "role", "is_staff")
    list_filter = ("role", "organization", "is_staff", "is_active")
    search_fields = ("phone", "first_name", "last_name")
    ordering = ("phone",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Organization", {"fields": ("organization", "role")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "phone",
                    "first_name",
                    "last_name",
                    "organization",
                    "role",
                    "password1",
                    "password2",
                ),
            },
        ),
    )