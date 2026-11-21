from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .services import generate_schedule
from .solver import SolverError

from .models import (
    Availability,
    EmployeeProfile,
    LeaveRequest,
    Schedule,
    ShiftAssignment,
    ShiftRequirement,
    ShiftType,
    Skill,
    SkillRequirement,
    UnfilledSlot,
    Holiday
)

@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")
    list_filter = ("organization",)
    search_fields = ("name",)


@admin.register(ShiftType)
class ShiftTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "start_time", "end_time", "is_night")
    list_filter = ("organization", "is_night")
    search_fields = ("name",)


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "max_weekly_hours", "max_monthly_hours", "is_active")
    list_filter = ("organization", "is_active")
    search_fields = ("user__phone", "user__first_name", "user__last_name")
    filter_horizontal = ("skills",)


class SkillRequirementInline(admin.TabularInline):
    model = SkillRequirement
    extra = 1


@admin.register(ShiftRequirement)
class ShiftRequirementAdmin(admin.ModelAdmin):
    list_display = ("shift_type", "weekday", "required_count", "organization")
    list_filter = ("organization", "weekday")
    inlines = [SkillRequirementInline]


@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = ("employee", "weekday", "start_time", "end_time", "kind", "organization")
    list_filter = ("organization", "kind", "weekday")


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "start_date", "end_date", "status", "organization")
    list_filter = ("organization", "status")
    readonly_fields = ("reviewed_by", "reviewed_at", "created_at")    


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ("__str__", "organization", "start_date", "end_date", "status")
    list_filter = ("organization", "status")
    readonly_fields = ("created_by", "created_at", "published_at")
    actions = ["generate"]

    @admin.action(description="Generate schedule (replaces existing assignments)")
    def generate(self, request, queryset):
        for schedule in queryset:
            try:
                solution = generate_schedule(schedule)
            except ValidationError as error:
                self.message_user(
                    request, f"{schedule}: {'; '.join(error.messages)}", messages.ERROR
                )
                continue
            except SolverError as error:
                self.message_user(request, f"{schedule}: {error}", messages.ERROR)
                continue
            missing = sum(s.missing_count for s in solution.shortages)
            self.message_user(
                request,
                f"{schedule}: {len(solution.assignments)} assignments, {missing} missing.",
                messages.SUCCESS if solution.is_complete else messages.WARNING,
            )


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ("date", "shift_type", "employee", "schedule", "is_manual")
    list_filter = ("organization", "schedule", "is_manual", "shift_type")
    date_hierarchy = "date"


@admin.register(UnfilledSlot)
class UnfilledSlotAdmin(admin.ModelAdmin):
    list_display = ("date", "shift_type", "skill", "missing_count", "schedule")
    list_filter = ("organization", "schedule")


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "organization")
    list_filter = ("organization",)
    date_hierarchy = "date"    