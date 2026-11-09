from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from organizations.models import TenantModel


class Skill(TenantModel):
    """A skill an employee can have and a shift can require (e.g. cashier)."""

    name = models.CharField(_("name"), max_length=100)

    class Meta:
        verbose_name = _("skill")
        verbose_name_plural = _("skills")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_skill_name_per_organization",
            ),
        ]

    def __str__(self):
        return self.name


class ShiftType(TenantModel):
    """A kind of shift (morning, evening, night) with start and end time."""

    name = models.CharField(_("name"), max_length=100)
    start_time = models.TimeField(_("start time"))
    end_time = models.TimeField(_("end time"))
    is_night = models.BooleanField(
        _("night shift"),
        default=False,
        help_text=_("Night shifts are distributed fairly between employees."),
    )

    class Meta:
        verbose_name = _("shift type")
        verbose_name_plural = _("shift types")
        ordering = ["start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_shifttype_name_per_organization",
            ),
            models.CheckConstraint(
                condition=~models.Q(start_time=models.F("end_time")),
                name="shifttype_start_differs_from_end",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.start_time:%H:%M}-{self.end_time:%H:%M})"

    @property
    def crosses_midnight(self):
        return self.end_time < self.start_time

    @property
    def duration(self):
        start = datetime.combine(date.min, self.start_time)
        end = datetime.combine(date.min, self.end_time)
        if end <= start:
            end += timedelta(days=1)
        return end - start

    @property
    def duration_hours(self):
        return self.duration.total_seconds() / 3600


class Weekday(models.IntegerChoices):
    # Values follow Python's date.weekday() (Monday=0), but the Iranian
    # week starts on Saturday, so the choices are listed in that order.
    SATURDAY = 5, _("Saturday")
    SUNDAY = 6, _("Sunday")
    MONDAY = 0, _("Monday")
    TUESDAY = 1, _("Tuesday")
    WEDNESDAY = 2, _("Wednesday")
    THURSDAY = 3, _("Thursday")
    FRIDAY = 4, _("Friday")


class EmployeeProfile(TenantModel):
    """Scheduling data of an employee: skills and working-hour limits."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_profile",
        verbose_name=_("user"),
    )
    skills = models.ManyToManyField(
        Skill,
        blank=True,
        related_name="employees",
        verbose_name=_("skills"),
    )
    max_weekly_hours = models.PositiveSmallIntegerField(
        _("max weekly hours"), default=44, validators=[MinValueValidator(1)]
    )
    max_monthly_hours = models.PositiveSmallIntegerField(
        _("max monthly hours"), null=True, blank=True, validators=[MinValueValidator(1)]
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("employee profile")
        verbose_name_plural = _("employee profiles")
        ordering = ["user__last_name", "user__first_name"]

    def __str__(self):
        return str(self.user)

    def clean(self):
        super().clean()
        user = getattr(self, "user", None)
        if user is not None and self.organization_id:
            if user.organization_id != self.organization_id:
                raise ValidationError(
                    {"user": _("The user must belong to the same organization.")}
                )


class ShiftRequirement(TenantModel):
    """How many people a shift type needs on a given weekday."""

    shift_type = models.ForeignKey(
        ShiftType,
        on_delete=models.CASCADE,
        related_name="requirements",
        verbose_name=_("shift type"),
    )
    weekday = models.PositiveSmallIntegerField(_("weekday"), choices=Weekday.choices)
    required_count = models.PositiveSmallIntegerField(
        _("required staff"), validators=[MinValueValidator(1)]
    )

    class Meta:
        verbose_name = _("shift requirement")
        verbose_name_plural = _("shift requirements")
        ordering = ["shift_type", "weekday"]
        constraints = [
            models.UniqueConstraint(
                fields=["shift_type", "weekday"],
                name="unique_requirement_per_shift_and_weekday",
            ),
            models.CheckConstraint(
                condition=models.Q(required_count__gte=1),
                name="requirement_count_at_least_one",
            ),
        ]

    def __str__(self):
        return f"{self.shift_type.name} / {self.get_weekday_display()}: {self.required_count}"

    def clean(self):
        super().clean()
        shift_type = getattr(self, "shift_type", None)
        if shift_type is not None and self.organization_id:
            if shift_type.organization_id != self.organization_id:
                raise ValidationError(
                    {"shift_type": _("The shift type must belong to the same organization.")}
                )


class SkillRequirement(models.Model):
    """At least min_count people with a given skill for a shift requirement.

    Not a TenantModel on purpose: it is a child of ShiftRequirement and
    gets its organization through that parent.
    """

    requirement = models.ForeignKey(
        ShiftRequirement,
        on_delete=models.CASCADE,
        related_name="skill_requirements",
        verbose_name=_("requirement"),
    )
    skill = models.ForeignKey(
        Skill,
        on_delete=models.CASCADE,
        related_name="requirements",
        verbose_name=_("skill"),
    )
    min_count = models.PositiveSmallIntegerField(
        _("minimum staff with this skill"), default=1, validators=[MinValueValidator(1)]
    )

    class Meta:
        verbose_name = _("skill requirement")
        verbose_name_plural = _("skill requirements")
        constraints = [
            models.UniqueConstraint(
                fields=["requirement", "skill"],
                name="unique_skill_per_requirement",
            ),
            models.CheckConstraint(
                condition=models.Q(min_count__gte=1),
                name="skill_requirement_count_at_least_one",
            ),
        ]

    def __str__(self):
        return f"{self.skill} x{self.min_count}"

    def clean(self):
        super().clean()
        requirement = getattr(self, "requirement", None)
        skill = getattr(self, "skill", None)
        if requirement is None:
            return
        if skill is not None and skill.organization_id != requirement.organization_id:
            raise ValidationError(
                {"skill": _("The skill must belong to the same organization.")}
            )
        if self.min_count and self.min_count > requirement.required_count:
            raise ValidationError(
                {"min_count": _("Cannot exceed the total required staff of the shift.")}
            )    