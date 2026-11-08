from django.contrib.auth import login
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView

from organizations.mixins import OrganizationRequiredMixin

from .forms import SignupForm


class SignupView(FormView):
    template_name = "accounts/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return super().form_valid(form)


class DashboardView(OrganizationRequiredMixin, TemplateView):
    template_name = "accounts/dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        # System admins (no organization) belong in the admin panel.
        if (
            request.user.is_authenticated
            and request.user.organization is None
            and request.user.is_staff
        ):
            return redirect("admin:index")
        return super().dispatch(request, *args, **kwargs)