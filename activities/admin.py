from django.contrib import admin

from accounts.admin import NoDeleteAdminMixin

from .models import (
    Activity,
    ActivityAuditLog,
    ActivityResourcePerson,
    ActivityStatus,
    ActivityType,
    ParticipantCategory,
    ResourcePerson,
)


@admin.register(ActivityStatus)
class ActivityStatusAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "description")


@admin.register(ActivityType)
class ActivityTypeAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)


@admin.register(ParticipantCategory)
class ParticipantCategoryAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "description")


class ActivityResourcePersonInline(admin.TabularInline):
    model = ActivityResourcePerson
    extra = 0


@admin.register(Activity)
class ActivityAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    """Activities are never deleted; change their status instead."""

    list_display = ("title", "activity_type", "status", "start_date", "end_date", "max_slots", "created_by")
    list_filter = ("status", "activity_type", "participant_category")
    search_fields = ("title", "venue")
    date_hierarchy = "start_date"
    inlines = [ActivityResourcePersonInline]


@admin.register(ActivityAuditLog)
class ActivityAuditLogAdmin(admin.ModelAdmin):
    """Read-only: audit rows are written by services, never edited by hand."""

    list_display = ("activity", "field_changed", "old_value", "new_value", "changed_by", "changed_at")
    list_filter = ("field_changed",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ResourcePerson)
class ResourcePersonAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "contact_number")
    search_fields = ("name", "email")


@admin.register(ActivityResourcePerson)
class ActivityResourcePersonAdmin(admin.ModelAdmin):
    list_display = ("activity", "resource_person", "topic")
