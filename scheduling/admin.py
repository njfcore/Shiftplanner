from django.contrib import admin

from .models import EmployeeProfile, ShiftRequirement, ShiftType, Skill, SkillRequirement


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