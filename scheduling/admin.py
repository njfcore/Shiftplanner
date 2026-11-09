from django.contrib import admin

from .models import ShiftType, Skill


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