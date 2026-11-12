"""Admin for roles and TBI members. Only members whose role can manage users (System Administrator) open the admin site.

Member changes in the Django admin go through accounts.services, so the same rules
apply as in the Admin Console: temporary password + invitation, no deleting, an Admin
cannot deactivate themselves, and at least one active Admin always remains.
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.options import ActionLocation

from config.mail import email_sent_message

from . import services
from .models import Role, TBIMember
from .permissions import Permission, has_permission


class NoDeleteAdminMixin:
    """Hides Delete (button and bulk action) for rows that must never be deleted:
    lookups, signatories, templates, activities and members."""

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class ReadOnlyAdminMixin:
    """View only: no add, change or delete. For lookups whose fixed names the code relies on."""

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class ActivityDataAdminMixin:
    """Activity data (activities, resource-person links, registrations) in the Django admin: view only, with no
    actions, for a member without can_create_activity. The System Administrator opens the admin site (it manages
    users and master data) but must not change activity data (separation of duties, ASSUMPTIONS 339). The services
    refuse anyway; this keeps the admin from offering what would only be refused."""

    def _can_change_activity_data(self, request) -> bool:
        return has_permission(request.user, Permission.CREATE_ACTIVITY)

    def has_add_permission(self, request, *args) -> bool:
        return self._can_change_activity_data(request) and super().has_add_permission(request, *args)

    def has_change_permission(self, request, obj=None) -> bool:
        return self._can_change_activity_data(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None) -> bool:
        return self._can_change_activity_data(request) and super().has_delete_permission(request, obj)

    def has_view_permission(self, request, obj=None) -> bool:
        return request.user.is_active and request.user.is_staff

    def get_actions(self, request, action_location=ActionLocation.CHANGE_LIST):
        if not self._can_change_activity_data(request):
            return {}
        return super().get_actions(request, action_location=action_location)


class RoleAdminForm(forms.ModelForm):
    """Only the three permission flags can change. The separation-of-duties rule (check_role_flags) and the
    last-System-Administrator rule are checked here so the errors show on the form; the save goes through
    accounts.services.update_role_permissions (which checks both again and writes the audit log)."""

    class Meta:
        model = Role
        fields = ("can_create_activity", "can_manage_users", "can_approve_documents", "can_approve_certificates")

    def clean(self) -> dict:
        cleaned = super().clean()
        try:
            services.check_role_flags(
                can_create_activity=bool(cleaned.get("can_create_activity")),
                can_manage_users=bool(cleaned.get("can_manage_users")),
                can_approve_documents=bool(cleaned.get("can_approve_documents")),
                can_approve_certificates=bool(cleaned.get("can_approve_certificates")),
            )
            if self.instance.can_manage_users and not cleaned.get("can_manage_users"):
                services.ensure_admins_remain_without_role(self.instance)
        except services.MemberManagementError as error:
            raise forms.ValidationError(str(error)) from error
        return cleaned


@admin.register(Role)
class RoleAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    """Roles: only the four flags (can_create_activity, can_manage_users, can_approve_documents,
    can_approve_certificates) can be edited.
    No adding, renaming or deleting: the code relies on the five fixed role names."""

    form = RoleAdminForm
    list_display = ("role_name", "can_create_activity", "can_manage_users", "can_approve_documents",
                    "can_approve_certificates", "created_at")
    fields = ("role_name", "description", "can_create_activity", "can_manage_users", "can_approve_documents",
              "can_approve_certificates", "created_at")
    readonly_fields = ("role_name", "description", "created_at")

    def has_add_permission(self, request) -> bool:
        return False

    def save_model(self, request, obj: Role, form, change: bool) -> None:
        saved = Role.objects.get(pk=obj.pk)
        services.update_role_permissions(
            request.user,
            saved,
            can_create_activity=form.cleaned_data["can_create_activity"],
            can_manage_users=form.cleaned_data["can_manage_users"],
            can_approve_documents=form.cleaned_data["can_approve_documents"],
            can_approve_certificates=form.cleaned_data["can_approve_certificates"],
        )


class TBIMemberAddForm(forms.ModelForm):
    """New member: no password field. A temporary password is generated and emailed."""

    class Meta:
        model = TBIMember
        fields = ("email", "name", "role")

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].strip().lower()
        if TBIMember.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A member with this email already exists.")
        return email


class TBIMemberChangeForm(forms.ModelForm):
    class Meta:
        model = TBIMember
        fields = ("email", "name", "role")

    def clean(self) -> dict:
        cleaned = super().clean()
        new_role = cleaned.get("role")
        # self.instance still has the old role here.
        if new_role and new_role.pk != self.instance.role_id and not new_role.can_manage_users:
            try:
                services.ensure_another_admin_remains(self.instance)
            except services.MemberManagementError as error:
                raise forms.ValidationError(str(error)) from error
        return cleaned


@admin.register(TBIMember)
class TBIMemberAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    """Members: create, edit name/email/role, and (de)activate through actions."""

    list_display = ("name", "email", "role", "is_active", "must_change_password", "last_login")
    list_filter = ("role", "is_active")
    search_fields = ("name", "email")
    ordering = ("name",)
    readonly_fields = ("is_active", "must_change_password", "is_superuser", "last_login", "created_at", "updated_at")
    actions = ["deactivate_selected", "reactivate_selected", "reset_password_selected"]

    def get_form(self, request, obj=None, **kwargs):
        kwargs["form"] = TBIMemberChangeForm if obj else TBIMemberAddForm
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("email", "name", "role")
        return ("email", "name", "role", *self.readonly_fields)

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields if obj else ()

    def save_model(self, request, obj: TBIMember, form, change: bool) -> None:
        if change:
            new_role = obj.role
            if "role" in form.changed_data:  # through the service: the last-admin guard and the audit log
                obj.role = TBIMember.objects.select_related("role").get(pk=obj.pk).role
                super().save_model(request, obj, form, change)  # name / email
                services.change_member_role(request.user, obj, new_role)
            else:
                super().save_model(request, obj, form, change)
        else:
            services.save_new_member_with_invitation(obj)
            self.message_user(request, email_sent_message(f"Invitation with a temporary password sent to {obj.email}."))

    @admin.action(description="Deactivate selected members")
    def deactivate_selected(self, request, queryset) -> None:
        for member in queryset:
            try:
                services.deactivate_member(request.user, member)
            except services.MemberManagementError as error:
                self.message_user(request, f"{member.email}: {error}", level=messages.ERROR)

    @admin.action(description="Reactivate selected members")
    def reactivate_selected(self, request, queryset) -> None:
        for member in queryset:
            services.reactivate_member(request.user, member)

    @admin.action(description="Reset password (email a new temporary password)")
    def reset_password_selected(self, request, queryset) -> None:
        for member in queryset:
            try:
                services.reset_member_password(request.user, member)
            except services.MemberManagementError as error:
                self.message_user(request, f"{member.email}: {error}", level=messages.ERROR)
            else:
                self.message_user(request, email_sent_message(f"A new temporary password was emailed to {member.email}."))
