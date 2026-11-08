from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


class OrganizationRequiredMixin(LoginRequiredMixin):
    """The user must be logged in and belong to an active organization."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            organization = request.organization
            if organization is None or not organization.is_active:
                raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)