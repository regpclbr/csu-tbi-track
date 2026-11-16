from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from accounts.admin import ActivityDataAdminMixin, NoDeleteAdminMixin, ReadOnlyAdminMixin
from accounts.permissions import Permission, require_permission

from . import services
from .formatting import changed_by_name
from .models import (
    Activity,
    ActivityAuditLog,
    ActivityDeletion,
    ActivityResourcePerson,
    ActivityStatus,
    ActivityType,
    ParticipantCategory,
    ResourcePerson,
)


@admin.register(ActivityStatus)
class ActivityStatusAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only: the app relies on the four fixed status names."""

    list_display = ("name", "description")


@admin.register(ActivityType)
class ActivityTypeAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)


class ParticipantCategoryForm(forms.ModelForm):
    class Meta:
        model = ParticipantCategory
        fields = ("name", "description")

    def clean_name(self) -> str:
        """Trimmed, unique ignoring case; standard categories keep their name."""
        try:
            return services.validate_category_name(self.cleaned_data["name"], self.instance)
        except ValidationError as error:
            raise ValidationError(error.message_dict["name"]) from error


@admin.register(ParticipantCategory)
class ParticipantCategoryAdmin(admin.ModelAdmin):
    """The Admin can add categories and edit descriptions.

    The standard ones (Faculty, Incubatees, MSMEs) cannot be renamed or deleted. An added
    category can be renamed, and deleted only while no activity uses it.
    """

    form = ParticipantCategoryForm
    list_display = ("name", "description", "standard", "activities_using")
    actions = None  # no bulk delete: delete one at a time, so each gets its check

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(used_by=Count("activities"))

    @admin.display(description="Standard", boolean=True)
    def standard(self, obj: ParticipantCategory) -> bool:
        return services.is_seeded_category(obj)

    @admin.display(description="Activities using it", ordering="used_by")
    def activities_using(self, obj: ParticipantCategory) -> int:
        return obj.used_by

    def get_readonly_fields(self, request, obj=None):
        return ("name",) if obj is not None and services.is_seeded_category(obj) else ()

    def has_delete_permission(self, request, obj=None) -> bool:
        return obj is not None and not services.is_seeded_category(obj)

    def save_model(self, request, obj: ParticipantCategory, form, change: bool) -> None:
        services.save_participant_category(request.user, obj, obj.name, obj.description)

    def delete_view(self, request, object_id, extra_context=None):
        """Explain clearly why a category in use cannot be deleted (instead of an error page)."""
        category = self.get_object(request, object_id)
        if category is not None and (reason := services.why_category_cannot_be_deleted(category)):
            self.message_user(request, reason, level=messages.ERROR)
            return redirect(reverse("admin:activities_participantcategory_change", args=[category.pk]))
        return super().delete_view(request, object_id, extra_context)

    def delete_model(self, request, obj: ParticipantCategory) -> None:
        services.delete_participant_category(request.user, obj)


def _is_conducted(activity: Activity | None) -> bool:
    return activity is not None and activity.status.name == ActivityStatus.CONDUCTED


class ActivityResourcePersonInline(ActivityDataAdminMixin, admin.TabularInline):
    """Add or remove resource persons (through services, so each change is audited).
    Existing links are read-only; to change a topic, remove the person and add them again."""

    model = ActivityResourcePerson
    extra = 0

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_add_permission(self, request, obj=None) -> bool:
        return obj is not None and not _is_conducted(obj)

    def has_delete_permission(self, request, obj=None) -> bool:
        return obj is not None and not _is_conducted(obj)


class ActivityAdminForm(forms.ModelForm):
    """Runs the same validation as activities.services, so errors show on the form."""

    participant_categories = forms.ModelMultipleChoiceField(
        queryset=ParticipantCategory.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Choose at least one participant category."},
        help_text="Who the activity is for. Choose one or more.",
    )

    class Meta:
        model = Activity
        fields = services.EDITABLE_FIELDS

    def clean(self) -> dict:
        cleaned = super().clean()
        if self.errors:
            return cleaned
        saved = Activity.objects.get(pk=self.instance.pk) if self.instance.pk else None
        values = services.activity_values(saved) if saved else {}
        submitted = {field: cleaned[field] for field in services.EDITABLE_FIELDS if field in cleaned}
        if services.CATEGORIES in submitted:
            submitted[services.CATEGORIES] = frozenset(submitted[services.CATEGORIES])
        changed = {field for field, value in submitted.items() if saved is None or value != values[field]}
        values.update(submitted)
        try:
            # Same rules as the service, including "not in the past" for changed dates only.
            services.validate_activity_values(values, saved, changed_fields=changed)
        except ValidationError as error:  # each message is shown under its own field
            raise ValidationError(error.message_dict) from error
        return cleaned


@admin.register(Activity)
class ActivityAdmin(ActivityDataAdminMixin, NoDeleteAdminMixin, admin.ModelAdmin):
    """Every change goes through activities.services, so the audit log is never skipped.

    Activities are never deleted. Status is shown as text and changed with the
    "Change status" button (on the edit page) or the list actions, never by editing.
    """

    change_form_template = "admin/activities/activity/change_form.html"

    form = ActivityAdminForm
    list_display = ("title", "activity_type", "categories", "status", "start_date", "end_date", "max_slots", "created_by")
    list_filter = ("status", "activity_type", "participant_categories")
    search_fields = ("title", "venue")
    date_hierarchy = "start_date"
    actions = [
        "move_to_scheduled", "move_to_ongoing", "move_to_conducted", "move_to_draft",
        "create_forms", "send_evaluation_links",
    ]

    def get_inlines(self, request, obj=None):
        # Resource persons can only be linked once the activity exists.
        return [ActivityResourcePersonInline] if obj is not None else []

    def get_fields(self, request, obj=None):
        if obj is None:
            return services.EDITABLE_FIELDS
        return ("current_status", *services.EDITABLE_FIELDS, "created_by")

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        if _is_conducted(obj):  # Conducted is read-only
            return ("current_status", *services.EDITABLE_FIELDS, "created_by")
        return ("current_status", "created_by")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("participant_categories")

    @admin.display(description="Participant categories")
    def categories(self, obj: Activity) -> str:
        return ", ".join(sorted(category.name for category in obj.participant_categories.all()))

    @admin.display(description="Status")
    def current_status(self, obj: Activity) -> str:
        """Plain text, not a link to the status lookup row."""
        return obj.status.name

    # --- "Change status" page (button on the edit page) -----------------------------------

    def get_urls(self):
        custom = [
            path(
                "<path:object_id>/change-status/",
                self.admin_site.admin_view(self.change_status_view),
                name="activities_activity_change_status",
            )
        ]
        return custom + super().get_urls()

    def change_status_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """A small form offering only the statuses this activity can move to right now."""
        require_permission(request.user, Permission.CREATE_ACTIVITY)  # 403 for the System Administrator
        activity = get_object_or_404(Activity, pk=object_id)
        change_url = reverse("admin:activities_activity_change", args=[activity.pk])
        if request.method == "POST":
            new_status = request.POST.get("status", "")
            old_status = activity.status.name
            try:
                services.change_status(request.user, activity, new_status)
            except services.ActivityError as error:
                self.message_user(request, f"Status not changed: {error}", level=messages.ERROR)
                return redirect(request.path)
            self.message_user(
                request, f'Status of "{activity.title}" changed from {old_status} to {new_status}.', messages.SUCCESS
            )
            return redirect(change_url)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": f"Change status: {activity.title}",
            "activity": activity,
            "current_status": activity.status.name,
            "next_statuses": services.allowed_next_statuses(activity),
            "registered": services.registered_count(activity),
            "change_url": change_url,
        }
        return TemplateResponse(request, "admin/activities/activity/change_status.html", context)

    def save_model(self, request, obj: Activity, form, change: bool) -> None:
        if not change:
            services.save_new_activity(request.user, obj, form.cleaned_data[services.CATEGORIES])
            return
        changed = {field: form.cleaned_data[field] for field in form.changed_data if field in services.EDITABLE_FIELDS}
        if changed:
            saved = Activity.objects.get(pk=obj.pk)
            services.update_activity(request.user, saved, changed)

    def save_related(self, request, form, formsets, change: bool) -> None:
        """Categories are already saved by the service (with the audit row), so skip
        form.save_m2m(); only the resource-person inline is saved here."""
        for formset in formsets:
            self.save_formset(request, form, formset, change=change)

    def save_formset(self, request, form, formset, change: bool) -> None:
        if formset.model is not ActivityResourcePerson:
            return super().save_formset(request, form, formset, change)
        new_links = formset.save(commit=False)  # nothing is written yet
        for link in formset.deleted_objects:
            services.remove_resource_person(request.user, form.instance, link.resource_person)
        for link in new_links:
            services.add_resource_person(request.user, form.instance, link.resource_person, link.topic)

    def _move(self, request, queryset, status_name: str) -> None:
        """List action: try each selected activity and report every success and failure."""
        moved = []
        for activity in queryset:
            try:
                services.change_status(request.user, activity, status_name)
                moved.append(activity.title)
            except services.ActivityError as error:
                self.message_user(request, f'"{activity.title}": {error}', level=messages.ERROR)
        if moved:
            titles = ", ".join(f'"{title}"' for title in moved)
            self.message_user(request, f"Status changed to {status_name} for {titles}.", messages.SUCCESS)

    @admin.action(description="Change status to Scheduled")
    def move_to_scheduled(self, request, queryset) -> None:
        self._move(request, queryset, ActivityStatus.SCHEDULED)

    @admin.action(description="Change status to Ongoing")
    def move_to_ongoing(self, request, queryset) -> None:
        self._move(request, queryset, ActivityStatus.ONGOING)

    @admin.action(description="Change status to Conducted")
    def move_to_conducted(self, request, queryset) -> None:
        self._move(request, queryset, ActivityStatus.CONDUCTED)

    @admin.action(description="Change status back to Draft")
    def move_to_draft(self, request, queryset) -> None:
        self._move(request, queryset, ActivityStatus.DRAFT)

    # --- Forms and evaluation links (registrations.services) ---------------------------

    @admin.action(description="Create registration and evaluation forms (from defaults)")
    def create_forms(self, request, queryset) -> None:
        from registrations import services as registration_services
        from registrations.models import FormType

        for activity in queryset:
            for form_type in FormType.values:
                try:
                    form = registration_services.create_form(request.user, activity, form_type)
                except registration_services.RegistrationError as error:
                    self.message_user(request, f'"{activity.title}": {error}', level=messages.WARNING)
                else:
                    self.message_user(
                        request, f'"{activity.title}": {form.title} created with {form.fields.count()} fields.', messages.SUCCESS
                    )

    @admin.action(description="Send evaluation links to attendees who have not evaluated")
    def send_evaluation_links(self, request, queryset) -> None:
        from registrations import services as registration_services

        for activity in queryset:
            try:
                sent = registration_services.send_evaluation_links(request.user, activity)
            except registration_services.RegistrationError as error:
                self.message_user(request, f'"{activity.title}": {error}', level=messages.ERROR)
            else:
                self.message_user(request, f'"{activity.title}": {sent} evaluation link(s) sent.', messages.SUCCESS)


@admin.register(ActivityAuditLog)
class ActivityAuditLogAdmin(admin.ModelAdmin):
    """Read-only: audit rows are written by services, never edited by hand."""

    list_display = ("activity", "field_changed", "old_value", "new_value", "who", "changed_at")
    list_filter = ("field_changed",)

    @admin.display(description="Changed by")
    def who(self, obj: ActivityAuditLog) -> str:
        return changed_by_name(obj)  # "System" for an automatic status change

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ResourcePerson)
class ResourcePersonAdmin(ActivityDataAdminMixin, admin.ModelAdmin):
    list_display = ("name", "email", "contact_number")
    search_fields = ("name", "email")


@admin.register(ActivityResourcePerson)
class ActivityResourcePersonAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only. Add or remove resource persons on the activity's page (audited)."""

    list_display = ("activity", "resource_person", "topic")


@admin.register(ActivityDeletion)
class ActivityDeletionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Records of deleted activities (ASSUMPTIONS 389): read only, never changed or removed."""

    list_display = ("activity_title", "deleted_activity_id", "start_date", "deleted_by", "deleted_at")
    search_fields = ("activity_title",)
