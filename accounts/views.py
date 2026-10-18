"""Staff login, logout, password change, and the Admin Console user API.

Views are thin: they read input, call accounts.services, and return a response.
The HTML pages here are temporary plain forms; the real pages come in the frontend phase.
"""

import json

from django.conf import settings
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from . import services
from .forms import LoginForm, PasswordChangeForm
from .models import Role, TBIMember
from .permissions import Permission, is_staff_member, permission_required, staff_login_required

# --- Login, logout, password change (temporary HTML forms) -------------------------


def _safe_next_url(request: HttpRequest) -> str | None:
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
        return next_url
    return None


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """Staff login with email and password."""
    if is_staff_member(request.user):
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            member = services.authenticate_member(
                request, form.cleaned_data["email"], form.cleaned_data["password"]
            )
        except services.LoginError as error:
            form.add_error(None, str(error))
        else:
            login(request, member)
            if member.must_change_password:
                return redirect("accounts:password_change")
            return redirect(_safe_next_url(request) or settings.LOGIN_REDIRECT_URL)

    return render(request, "accounts/login.html", {"form": form, "next": _safe_next_url(request) or ""})


def admin_login_redirect(request: HttpRequest) -> HttpResponse:
    """Send the Django admin's own login page to the staff login, so the lockout always applies."""
    return redirect_to_login(request.GET.get("next") or "/admin/")


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Log out (POST only, so a link or image on another site cannot log you out)."""
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


@staff_login_required
@require_http_methods(["GET", "POST"])
def password_change_view(request: HttpRequest) -> HttpResponse:
    """Change your own password. Required on first sign-in."""
    form = PasswordChangeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.change_password(
                request.user, form.cleaned_data["old_password"], form.cleaned_data["new_password1"]
            )
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
        else:
            update_session_auth_hash(request, request.user)  # stay logged in
            return redirect(settings.LOGIN_REDIRECT_URL)

    return render(request, "accounts/password_change.html", {"form": form})


@staff_login_required
def home_view(request: HttpRequest) -> HttpResponse:
    """Temporary landing page until the dashboard is built (Step 10 / frontend)."""
    return render(request, "accounts/home.html")


# --- Admin Console: user management JSON API -----------------------------------------


def _member_json(member: TBIMember) -> dict:
    return {
        "member_id": member.pk,
        "name": member.name,
        "email": member.email,
        "role": member.role.role_name,
        "role_id": member.role_id,
        "is_active": member.is_active,
        "must_change_password": member.must_change_password,
        "last_login": member.last_login.isoformat() if member.last_login else None,
    }


def _json_body(request: HttpRequest) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError as error:
        raise services.MemberManagementError("Request body must be valid JSON.") from error
    if not isinstance(data, dict):
        raise services.MemberManagementError("Request body must be a JSON object.")
    return data


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


@permission_required(Permission.MANAGE_USERS)
@require_http_methods(["GET"])
def roles_api(request: HttpRequest) -> JsonResponse:
    """List roles (for the role picker)."""
    roles = [
        {
            "role_id": r.pk,
            "role_name": r.role_name,
            "can_create_activity": r.can_create_activity,
            "can_manage_users": r.can_manage_users,
        }
        for r in Role.objects.all()
    ]
    return JsonResponse({"roles": roles})


@permission_required(Permission.MANAGE_USERS)
@require_http_methods(["GET", "POST"])
def members_api(request: HttpRequest) -> JsonResponse:
    """GET: list members. POST {name, email, role_id}: create a member and email an invitation."""
    if request.method == "GET":
        members = TBIMember.objects.select_related("role").order_by("name")
        return JsonResponse({"members": [_member_json(m) for m in members]})

    try:
        data = _json_body(request)
        name, email, role_id = data.get("name", ""), data.get("email", ""), data.get("role_id")
        if not name.strip() or not email.strip() or not role_id:
            return _error("name, email and role_id are required.")
        role = Role.objects.filter(pk=role_id).first()
        if role is None:
            return _error("Unknown role_id.")
        member = services.create_member(request.user, name=name, email=email, role=role)
    except services.MemberManagementError as error:
        return _error(str(error))
    return JsonResponse({"member": _member_json(member)}, status=201)


@permission_required(Permission.MANAGE_USERS)
@require_POST
def member_role_api(request: HttpRequest, member_id: int) -> JsonResponse:
    """POST {role_id}: change a member's role."""
    member = get_object_or_404(TBIMember, pk=member_id)
    try:
        role = Role.objects.filter(pk=_json_body(request).get("role_id")).first()
        if role is None:
            return _error("Unknown role_id.")
        services.change_member_role(request.user, member, role)
    except services.MemberManagementError as error:
        return _error(str(error))
    return JsonResponse({"member": _member_json(member)})


@permission_required(Permission.MANAGE_USERS)
@require_POST
def member_deactivate_api(request: HttpRequest, member_id: int) -> JsonResponse:
    """POST: deactivate a member."""
    member = get_object_or_404(TBIMember, pk=member_id)
    try:
        services.deactivate_member(request.user, member)
    except services.MemberManagementError as error:
        return _error(str(error))
    return JsonResponse({"member": _member_json(member)})


@permission_required(Permission.MANAGE_USERS)
@require_POST
def member_reactivate_api(request: HttpRequest, member_id: int) -> JsonResponse:
    """POST: reactivate a member."""
    member = get_object_or_404(TBIMember, pk=member_id)
    services.reactivate_member(request.user, member)
    return JsonResponse({"member": _member_json(member)})
