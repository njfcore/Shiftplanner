import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class Organization(models.Model):
    """Each customer of the system (cafe, store, clinic, ...) is an organization."""

    name = models.CharField(_("name"), max_length=150)
    slug = models.SlugField(_("slug"), max_length=20, unique=True, editable=False)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("organization")
        verbose_name_plural = _("organizations")
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = uuid.uuid4().hex[:12]
        super().save(*args, **kwargs)