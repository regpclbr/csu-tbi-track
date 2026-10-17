"""Admin for roles and TBI members. Only Admin-role members can open the admin site."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import Role, TBIMember


class NoDeleteAdminMixin:
    """For lookups, signatories and templates: never delete these rows, deactivate them."""

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Role)
class RoleAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("role_name", "can_create_activity", "can_manage_users", "created_at")


class TBIMemberCreationForm(AdminUserCreationForm):
    class Meta:
        model = TBIMember
        fields = ("email", "name", "role")


class TBIMemberChangeForm(UserChangeForm):
    class Meta:
        model = TBIMember
        fields = "__all__"


@admin.register(TBIMember)
class TBIMemberAdmin(UserAdmin):
    """Django's UserAdmin, adapted to email login and roles (no groups/permissions)."""

    form = TBIMemberChangeForm
    add_form = TBIMemberCreationForm
    list_display = ("name", "email", "role", "is_active", "must_change_password", "last_login")
    list_filter = ("role", "is_active")
    search_fields = ("name", "email")
    ordering = ("name",)
    filter_horizontal = ()
    readonly_fields = ("last_login", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name", "role")}),
        ("Status", {"fields": ("is_active", "must_change_password", "is_superuser")}),
        ("Dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "name", "role", "usable_password", "password1", "password2")}),
    )
