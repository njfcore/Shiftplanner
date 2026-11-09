from datetime import date, datetime, timedelta

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