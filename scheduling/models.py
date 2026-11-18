from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from organizations.models import TenantModel, TenantQuerySet


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


class Availability(TenantModel):
    """A weekly time window in which an employee can or cannot work."""

    class Kind(models.TextChoices):
        AVAILABLE = "available", _("Available")
        UNAVAILABLE = "unavailable", _("Unavailable")

    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name="availabilities",
        verbose_name=_("employee"),
    )
    weekday = models.PositiveSmallIntegerField(_("weekday"), choices=Weekday.choices)
    start_time = models.TimeField(_("start time"))
    end_time = models.TimeField(_("end time"))
    kind = models.CharField(
        _("kind"), max_length=12, choices=Kind.choices, default=Kind.AVAILABLE
    )

    class Meta:
        verbose_name = _("availability")
        verbose_name_plural = _("availabilities")
        ordering = ["employee", "weekday", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(start_time=models.F("end_time")),
                name="availability_start_differs_from_end",
            ),
        ]

    def __str__(self):
        return (
            f"{self.employee} / {self.get_weekday_display()} "
            f"{self.start_time:%H:%M}-{self.end_time:%H:%M} ({self.get_kind_display()})"
        )

    @property
    def crosses_midnight(self):
        return self.end_time < self.start_time

    def clean(self):
        super().clean()
        employee = getattr(self, "employee", None)
        if employee is not None and self.organization_id:
            if employee.organization_id != self.organization_id:
                raise ValidationError(
                    {"employee": _("The employee must belong to the same organization.")}
                )


class LeaveQuerySet(TenantQuerySet):
    def approved(self):
        return self.filter(status=LeaveRequest.Status.APPROVED)

    def covering(self, day):
        """Leave requests whose date range includes the given day."""
        return self.filter(start_date__lte=day, end_date__gte=day)


class LeaveRequest(TenantModel):
    """A leave request of an employee, reviewed by a manager."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        CANCELLED = "cancelled", _("Cancelled")

    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name="leave_requests",
        verbose_name=_("employee"),
    )
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"))
    reason = models.TextField(_("reason"), blank=True)
    status = models.CharField(
        _("status"), max_length=10, choices=Status.choices, default=Status.PENDING
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_leave_requests",
        verbose_name=_("reviewed by"),
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    objects = LeaveQuerySet.as_manager()

    class Meta:
        verbose_name = _("leave request")
        verbose_name_plural = _("leave requests")
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="leave_end_not_before_start",
            ),
        ]

    def __str__(self):
        return f"{self.employee}: {self.start_date} - {self.end_date} ({self.get_status_display()})"

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1

    def clean(self):
        super().clean()
        employee = getattr(self, "employee", None)
        if employee is not None and self.organization_id:
            if employee.organization_id != self.organization_id:
                raise ValidationError(
                    {"employee": _("The employee must belong to the same organization.")}
                )

    def _review(self, reviewer, new_status):
        if self.status != self.Status.PENDING:
            raise ValidationError(_("Only pending requests can be reviewed."))
        if reviewer.organization_id != self.organization_id:
            raise ValidationError(_("The reviewer must belong to the same organization."))
        self.status = new_status
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.save()

    def approve(self, reviewer):
        self._review(reviewer, self.Status.APPROVED)

    def reject(self, reviewer):
        self._review(reviewer, self.Status.REJECTED)


def _organization_errors(instance, field_names):
    """Return a ValidationError dict for related objects of another organization."""
    errors = {}
    if not instance.organization_id:
        return errors
    for name in field_names:
        related = getattr(instance, name, None)
        if related is not None and related.organization_id != instance.organization_id:
            errors[name] = _("This object must belong to the same organization.")
    return errors


class Schedule(TenantModel):
    """A generated schedule for a date range (a week, a month, ...)."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PUBLISHED = "published", _("Published")

    name = models.CharField(_("name"), max_length=150, blank=True)
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"))
    status = models.CharField(
        _("status"), max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_schedules",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    published_at = models.DateTimeField(_("published at"), null=True, blank=True)

    class Meta:
        verbose_name = _("schedule")
        verbose_name_plural = _("schedules")
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="schedule_end_not_before_start",
            ),
        ]

    def __str__(self):
        label = self.name or f"{self.start_date} - {self.end_date}"
        return f"{label} ({self.get_status_display()})"

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1

    def dates(self):
        """Every date of the schedule, in order."""
        return [self.start_date + timedelta(days=i) for i in range(self.days)]

    def clean(self):
        super().clean()
        errors = _organization_errors(self, ["created_by"])
        if errors:
            raise ValidationError(errors)


class ShiftAssignment(TenantModel):
    """One employee working one shift type on one date of a schedule.

    An employee works at most one shift per day in a schedule.
    """

    schedule = models.ForeignKey(
        Schedule,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name=_("schedule"),
    )
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.RESTRICT,
        related_name="assignments",
        verbose_name=_("employee"),
    )
    shift_type = models.ForeignKey(
        ShiftType,
        on_delete=models.RESTRICT,
        related_name="assignments",
        verbose_name=_("shift type"),
    )
    date = models.DateField(_("date"))
    is_manual = models.BooleanField(
        _("manually edited"),
        default=False,
        help_text=_("Set when a manager added or changed this assignment by hand."),
    )

    class Meta:
        verbose_name = _("shift assignment")
        verbose_name_plural = _("shift assignments")
        ordering = ["date", "shift_type__start_time", "employee"]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "employee", "date"],
                name="one_shift_per_employee_per_day",
            ),
        ]

    def __str__(self):
        return f"{self.date} {self.shift_type.name}: {self.employee}"

    @property
    def start_at(self):
        return datetime.combine(self.date, self.shift_type.start_time)

    @property
    def end_at(self):
        return self.start_at + self.shift_type.duration

    @property
    def hours(self):
        return self.shift_type.duration_hours

    def clean(self):
        super().clean()
        errors = _organization_errors(self, ["schedule", "employee", "shift_type"])
        schedule = getattr(self, "schedule", None)
        if schedule is not None and self.date:
            if not (schedule.start_date <= self.date <= schedule.end_date):
                errors["date"] = _("The date must fall within the schedule period.")
        if errors:
            raise ValidationError(errors)


class UnfilledSlot(TenantModel):
    """Staff the generator could not find for a shift, with the reason."""

    schedule = models.ForeignKey(
        Schedule,
        on_delete=models.CASCADE,
        related_name="unfilled_slots",
        verbose_name=_("schedule"),
    )
    date = models.DateField(_("date"))
    shift_type = models.ForeignKey(
        ShiftType,
        on_delete=models.RESTRICT,
        related_name="unfilled_slots",
        verbose_name=_("shift type"),
    )
    skill = models.ForeignKey(
        Skill,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="unfilled_slots",
        verbose_name=_("missing skill"),
        help_text=_("Empty means people are missing regardless of skill."),
    )
    missing_count = models.PositiveSmallIntegerField(
        _("missing staff"), validators=[MinValueValidator(1)]
    )
    reason = models.TextField(_("reason"), blank=True)

    class Meta:
        verbose_name = _("unfilled slot")
        verbose_name_plural = _("unfilled slots")
        ordering = ["date", "shift_type__start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(missing_count__gte=1),
                name="unfilled_missing_count_at_least_one",
            ),
        ]

    def __str__(self):
        return f"{self.date} {self.shift_type.name}: {self.missing_count} missing"

    def clean(self):
        super().clean()
        errors = _organization_errors(self, ["schedule", "shift_type", "skill"])
        schedule = getattr(self, "schedule", None)
        if schedule is not None and self.date:
            if not (schedule.start_date <= self.date <= schedule.end_date):
                errors["date"] = _("The date must fall within the schedule period.")
        if errors:
            raise ValidationError(errors)        