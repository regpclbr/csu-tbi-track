"""JSON API for startups. Views are thin: parse input, check access, call services.

- Staff (`/startups/api/`): any active staff member reads (item 12); sync and set-password
  links need can_manage_users.
- Startup portal (`/startups/portal/api/`): a logged-in startup only (separate session key).
- Public (`/api/startups/set-password/<token>/`): the signed, single-use set-password link. The
  emailed link opens the page URL /startups/set-password/<token>/ (frontend phase).
"""

import json
from collections.abc import Callable
from functools import wraps

from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from config.json import JsonResponse  # date-times in Manila time

from accounts.permissions import Permission, permission_required, staff_login_required
from accounts.services import LoginError
from certificates import services as certificate_services
from config.mail import email_sent_message
from config.parsing import parse_bool
from notifications import services as notification_services
from notifications.views import notification_json
from registrations import services as registration_services
from registrations.models import Registration

from . import services
from .decorators import startup_login_required
from .models import Startup
from .providers import ProviderNotAvailable


class BadRequest(Exception):
    """Invalid request body or query parameters."""


def api_errors(view: Callable) -> Callable:
    """Turn refusals and validation errors into JSON responses with clear messages."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> JsonResponse:
        try:
            return view(request, *args, **kwargs)
        except ValidationError as error:
            errors = error.message_dict if hasattr(error, "error_dict") else {"__all__": error.messages}
            return JsonResponse({"errors": errors}, status=400)
        except (services.StartupError, registration_services.RegistrationError, BadRequest) as error:
            return JsonResponse({"error": str(error)}, status=400)
        except ProviderNotAvailable as error:
            return JsonResponse({"error": str(error)}, status=503)

    return wrapper


def _json_body(request: HttpRequest) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError as error:
        raise BadRequest("Request body must be valid JSON.") from error
    if not isinstance(data, dict):
        raise BadRequest("Request body must be a JSON object.")
    return data


def profile_json(startup: Startup) -> dict:
    """Navigatu data (read-only in this system) and when it was last synced."""
    return {
        "startup_id": startup.pk,
        "navigatu_id": startup.navigatu_id,
        "name": startup.name,
        "email": startup.email,
        "contact_number": startup.contact_number,
        "maturity_stage": startup.maturity_stage,
        "last_assessment_at": startup.last_assessment_at,
        "is_active": startup.is_active,
        "has_password": bool(startup.password),
        "last_synced_at": startup.last_synced_at,
    }


def registration_json(registration: Registration) -> dict:
    activity = registration.activity
    return {
        "registration_id": registration.pk,
        "activity_id": activity.pk,
        "activity_title": activity.title,
        "activity_status": activity.status.name,
        "start_date": activity.start_date,
        "end_date": activity.end_date,
        "participant_category": registration.participant_category.name,
        "registered_at": registration.registration_date,
        "attendance_confirmed": registration.attendance_confirmed,
        "confirmation_method": registration.confirmation_method,
        "linked_to_startup": registration.startup_id is not None,  # False = older public registration (email match)
    }


# --- Staff side ------------------------------------------------------------------------------------


@staff_login_required
@require_GET
@api_errors
def startups_api(request: HttpRequest) -> JsonResponse:
    """GET ?q=&maturity_stage=&is_active=true|false&page="""
    filters = {
        "q": request.GET.get("q", ""),
        "maturity_stage": request.GET.get("maturity_stage", ""),
        "is_active": parse_bool(request.GET.get("is_active"), "is_active"),
    }
    result = services.list_startups(filters, page=request.GET.get("page", 1))
    return JsonResponse(
        {
            "startups": [profile_json(s) for s in result.items],
            "total": result.total,
            "page": result.page,
            "num_pages": result.num_pages,
        }
    )


@staff_login_required
@require_GET
def startup_detail_api(request: HttpRequest, startup_id: int) -> JsonResponse:
    startup = get_object_or_404(Startup, pk=startup_id)
    return JsonResponse(
        {
            "startup": profile_json(startup),
            "registrations": [registration_json(r) for r in services.registrations_for(startup)],
        }
    )


@permission_required(Permission.MANAGE_USERS)
@require_POST
@api_errors
def sync_api(request: HttpRequest) -> JsonResponse:
    """Run the Navigatu sync now (never forced: the safety guard always applies here)."""
    return JsonResponse({"result": services.sync_startups()})


@permission_required(Permission.MANAGE_USERS)
@require_POST
@api_errors
def send_password_link_api(request: HttpRequest, startup_id: int) -> JsonResponse:
    startup = get_object_or_404(Startup, pk=startup_id)
    services.send_password_link(request.user, startup)
    return JsonResponse({"message": email_sent_message(f"A set-password link was emailed to {startup.email}.")})


# --- Public: set-password link -----------------------------------------------------------------------


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
@api_errors
def set_password(request: HttpRequest, token: str) -> JsonResponse:
    """GET: check the link. POST {"password", "password_again"}: set the password (the link then stops working)."""
    if request.method == "GET":
        startup = services.startup_from_password_token(token)
        return JsonResponse({"valid": True, "name": startup.name, "email": startup.email})
    data = _json_body(request)
    startup = services.set_password_with_link(token, str(data.get("password", "")), str(data.get("password_again", "")))
    return JsonResponse({"message": "Your password is set. You can now sign in.", "email": startup.email})


# --- Startup portal ------------------------------------------------------------------------------------


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def portal_login(request: HttpRequest) -> JsonResponse:
    """GET: sets the CSRF cookie and says who is logged in. POST {"email", "password"}: log in."""
    if request.user.is_authenticated:
        return JsonResponse({"error": "Staff accounts cannot use the startup portal. Log out first."}, status=403)
    if request.method == "GET":
        startup = services.current_startup(request)
        return JsonResponse({"logged_in": startup is not None, "startup": profile_json(startup) if startup else None})
    try:
        data = _json_body(request)
    except BadRequest as error:
        return JsonResponse({"error": str(error)}, status=400)
    try:
        startup = services.authenticate_startup(request, str(data.get("email", "")), str(data.get("password", "")))
    except LoginError as error:
        return JsonResponse({"error": str(error)}, status=400)
    services.login_startup(request, startup)
    return JsonResponse({"logged_in": True, "startup": profile_json(startup)})


@require_POST
def portal_logout(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:  # never touches a staff session
        services.logout_startup(request)
    return JsonResponse({"logged_in": False})


@startup_login_required
@require_GET
def portal_me(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"startup": profile_json(request.startup)})


@startup_login_required
@require_GET
def portal_registrations(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"registrations": [registration_json(r) for r in services.registrations_for(request.startup)]})


@startup_login_required
@require_GET
def portal_open_activities(request: HttpRequest) -> JsonResponse:
    """GET: the same open activities as the public list, each with `already_registered` (this
    startup has a registration for it: linked, or an older public one with its email)."""
    mine = set(services.registrations_for(request.startup).values_list("activity_id", flat=True))
    rows = [
        {**registration_services.public_activity_summary(activity), "already_registered": activity.pk in mine}
        for activity in registration_services.open_activities()
    ]
    return JsonResponse({"activities": rows})


@startup_login_required
@require_POST
@api_errors
def portal_register(request: HttpRequest, activity_id: int) -> JsonResponse:
    """POST {"participant_category": id (optional if only one), "answers": {"<field_id>": value}}"""
    activity = registration_services.get_public_activity(activity_id)  # Scheduled only, like the public page
    if activity is None:
        raise Http404("Activity not found.")
    data = _json_body(request)
    registration = services.register_as_startup(
        request.startup, activity, participant_category=data.get("participant_category"), answers=data.get("answers") or {}
    )
    return JsonResponse({"registration": registration_json(registration)}, status=201)


def certificate_json(certificate) -> dict:
    return {
        "certificate_id": certificate.pk,
        "certificate_number": certificate.certificate_number,
        "activity_id": certificate.activity_id,
        "activity_title": certificate.activity.title,
        "issued_at": certificate.issued_at,
        "download_url": reverse("startups:portal_certificate_download", args=[certificate.pk]),
    }


@startup_login_required
@require_GET
def portal_certificates(request: HttpRequest) -> JsonResponse:
    """My certificates: Issued certificates of my registrations (linked or matched by email)."""
    return JsonResponse({"certificates": [certificate_json(c) for c in certificate_services.certificates_for_startup(request.startup)]})


@startup_login_required
@require_GET
def portal_certificate_download(request: HttpRequest, certificate_id: int) -> FileResponse:
    """Only my own Issued certificates. Anything else is 404 (never reveals that it exists)."""
    certificate = get_object_or_404(certificate_services.certificates_for_startup(request.startup), pk=certificate_id)
    if not certificate.file_path:
        raise Http404("The file is not available.")
    try:
        handle = certificate.file_path.open("rb")
    except (FileNotFoundError, ValueError) as error:
        raise Http404("The file is missing.") from error
    response = FileResponse(handle, as_attachment=True, filename=f"{certificate.certificate_number}.pdf")
    response["X-Content-Type-Options"] = "nosniff"
    return response


@startup_login_required
@require_GET
@api_errors
def portal_notifications(request: HttpRequest) -> JsonResponse:
    """GET ?page=&unread=true|false — newest first."""
    unread_only = parse_bool(request.GET.get("unread"), "unread", default=False)
    result = notification_services.list_notifications(request.startup, request.GET.get("page", 1), unread_only=unread_only)
    return JsonResponse(
        {
            "notifications": [notification_json(n) for n in result.items],
            "unread_count": notification_services.unread_count(request.startup),
            "total": result.total,
            "page": result.page,
            "num_pages": result.num_pages,
        }
    )


@startup_login_required
@require_GET
def portal_unread_count(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"unread_count": notification_services.unread_count(request.startup)})


@startup_login_required
@require_POST
def portal_mark_read(request: HttpRequest, notification_id: int) -> JsonResponse:
    notification = notification_services.mark_read(request.startup, notification_id)
    return JsonResponse({"notification": notification_json(notification)})


@startup_login_required
@require_POST
def portal_mark_all_read(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"marked_read": notification_services.mark_all_read(request.startup)})
