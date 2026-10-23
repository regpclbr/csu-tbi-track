"""Staff login, logout, password change, and the Admin Console user API.

Views are thin: they read input, call accounts.services, and return a response.
The HTML pages here are temporary plain forms; the real pages come in the frontend phase.
"""

import json

from django.conf import settings
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from config.network import client_ip
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from config.json import JsonResponse  # date-times in Manila time

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
        "last_login": member.last_login,  # shown in Manila time by config.json
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


def _text(data: dict, name: str) -> str:
    """A text value from the JSON body ("" when missing). Numbers, lists and the like are refused."""
    value = data.get(name, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise services.MemberManagementError(f"{name} must be text.")
    return value


def _role(data: dict) -> Role | None:
    """The role named by role_id (a whole number), or None when it does not exist."""
    role_id = data.get("role_id")
    if isinstance(role_id, bool) or not isinstance(role_id, int):
        raise services.MemberManagementError("role_id must be a number.")
    return Role.objects.filter(pk=role_id).first()


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
        name, email = _text(data, "name"), _text(data, "email")
        if not name.strip() or not email.strip() or data.get("role_id") in (None, ""):
            return _error("name, email and role_id are required.")
        role = _role(data)
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
        role = _role(_json_body(request))
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


@permission_required(Permission.MANAGE_USERS)
@require_POST
def member_reset_password_api(request: HttpRequest, member_id: int) -> JsonResponse:
    """POST: email the member a new temporary password; they must change it at the next sign-in."""
    member = get_object_or_404(TBIMember, pk=member_id)
    try:
        services.reset_member_password(request.user, member)
    except services.MemberManagementError as error:
        return _error(str(error))
    return JsonResponse({"member": _member_json(member), "message": f"A new temporary password was emailed to {member.email}."})


# --- Forgot password (staff, self-service; Django's password reset views) ------------------------
# Anyone can ask for a link by email. The answer is always the same, so it never reveals whether an
# email belongs to a member. Only active members with a usable password get an email (Django's rule).
# The link is single use and expires after PASSWORD_RESET_TIMEOUT. The new password must pass the
# same validators as everywhere else (AUTH_PASSWORD_VALIDATORS).


class ForgotPasswordView(auth_views.PasswordResetView):
    template_name = "accounts/password_forgot.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_forgot_done")

    @property
    def extra_email_context(self) -> dict:
        return {"site_url": settings.SITE_URL}  # the public address, not whatever Host header arrived

    def form_valid(self, form):
        """Rate limit (config/ratelimit.py): at most PASSWORD_RESET_MAX_REQUESTS_PER_EMAIL requests per
        email and PASSWORD_RESET_MAX_REQUESTS_PER_IP per IP address in PASSWORD_RESET_RATE_WINDOW_MINUTES.
        Over a limit the answer is the usual "check your email" page, so it reveals neither the limit nor
        whether the email exists; no email is sent, and a WARNING is logged with the IP only."""
        if services.password_reset_allowed(client_ip(self.request), form.cleaned_data["email"]):
            return super().form_valid(form)
        return HttpResponseRedirect(self.get_success_url())


class ForgotPasswordDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_forgot_done.html"


class NewPasswordForm(SetPasswordForm):
    """Django's set-password form (same validators); a password chosen this way is the member's
    own, so the first-sign-in change is no longer needed."""

    def save(self, commit: bool = True):
        self.user.must_change_password = False
        user = super().save(commit=commit)
        services.security_log.info("Password set with a forgot-password link: member_id=%s", user.pk)
        return user


class ResetPasswordConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = NewPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class ResetPasswordCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"
