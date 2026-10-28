"""The startup portal pages (phase F6): login, Overview, Open activities and registering, My registrations,
My certificates, Notifications and Profile, under /startups/portal/ (the JSON API stays at /startups/portal/api/).

Thin views: the rules are the services' (authenticate_startup / login_startup with the startup lockout, open_activities,
register_as_startup = registrations.services.register, evaluation_state, certificates_for_startup, notifications.services);
startups/portal_pages.py prepares what the templates show. Every page except the login needs a logged-in startup
(decorators.portal_page_required): a staff session gets 403, nobody logged in goes to the portal login.
"""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.services import LoginError
from certificates.services import certificates_for_startup
from documents.pages import safe_next
from notifications import services as notification_services
from registrations import public_pages
from registrations import services as registration_services
from registrations.models import FormType

from . import portal_pages as pages
from . import services
from .decorators import STAFF_REFUSED, portal_page_required

# Errors about the profile fields cannot be fixed on this page (they come from Navigatu): shown as one message.
PROFILE_FIELDS = ("name", "email", "contact_number")


def _render(request: HttpRequest, template: str, title: str, context: dict | None = None, status: int = 200) -> HttpResponse:
    return render(request, template, {"page_title": title, **(context or {})}, status=status)


def notice(request: HttpRequest, *, title: str, text: str, icon: str, tone: str = "info", status: int = 200, **extra) -> HttpResponse:
    """A friendly card inside the portal (closed registration, already registered, ...)."""
    context = {"title": title, "text": text, "icon": icon, "tone": tone, **extra}
    return _render(request, "portal/notice.html", title, context, status=status)


# --- Login and logout ----------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def login_page(request: HttpRequest) -> HttpResponse:
    """The startup login (same lockout rules and messages as staff, own counters: authenticate_startup)."""
    if request.user.is_authenticated:
        raise PermissionDenied(STAFF_REFUSED)
    next_url = pages.safe_portal_next(request.POST.get("next") or request.GET.get("next"))
    if services.current_startup(request) is not None:
        return redirect(next_url or "startups:portal_home")
    email, error = "", ""
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        try:
            startup = services.authenticate_startup(request, email, request.POST.get("password", ""))
        except LoginError as refused:
            error = str(refused)
        else:
            services.login_startup(request, startup)
            return redirect(next_url or "startups:portal_home")
    context = {"email": email, "error": error, "next": next_url or "", "contact_email": settings.TBI_CONTACT_EMAIL}
    return render(request, "portal/login.html", context, status=400 if error else 200)


@require_POST
def logout_page(request: HttpRequest) -> HttpResponse:
    """Log out of the portal (POST only). Never touches a staff session."""
    if not request.user.is_authenticated:
        services.logout_startup(request)
    return redirect("startups:portal_login_page")


# --- Overview and open activities ----------------------------------------------------------------


@portal_page_required
@require_GET
def home(request: HttpRequest) -> HttpResponse:
    return _render(request, "portal/home.html", "Overview", pages.overview(request.startup))


@portal_page_required
@require_GET
def open_activities(request: HttpRequest) -> HttpResponse:
    return _render(request, "portal/activities.html", "Open activities", {"cards": pages.open_cards(request.startup)})


@portal_page_required
@require_http_methods(["GET", "POST"])
def register_page(request: HttpRequest, activity_id: int) -> HttpResponse:
    """GET: the activity and its registration form, with my profile shown read-only. POST: register through
    services.register_as_startup (= registrations.services.register: the same rules as the public page)."""
    activity = registration_services.get_public_activity(activity_id)  # Scheduled only, as the portal API
    if activity is None:
        raise Http404("Activity not found.")
    startup = request.startup
    if activity.pk in pages.registered_activity_ids(startup):
        return notice(request, title="You're already registered", icon="circle-check", tone="success",
                      text="You have a registration for this activity. See it under My registrations.",
                      link_url=reverse("startups:portal_registrations_page"), link_label="My registrations")

    form = registration_services.get_form(activity, FormType.REGISTRATION)
    values: dict = {"participant_category": "", "answers": {}}
    errors: dict[str, str] = {}
    message, status = "", 200
    if request.method == "POST":
        values = {
            "participant_category": request.POST.get("participant_category", ""),
            "answers": public_pages.answers_from_post(form, request.POST),
        }
        try:
            services.register_as_startup(
                startup, activity, participant_category=values["participant_category"] or None, answers=values["answers"]
            )
        except registration_services.RegistrationError as refused:
            message, status = str(refused), 400
        except ValidationError as refused:
            errors, status = public_pages.flat_errors(refused.message_dict), 400
            profile = [errors.pop(key) for key in PROFILE_FIELDS if key in errors]
            if profile:
                message = " ".join(profile) + " Your profile comes from Navigatu: ask the TBI to correct it."
        else:
            public_pages.remember_registration(request, activity.pk)
            return redirect("startups:portal_registered", activity_id=activity.pk)

    reason = registration_services.registration_closed_reason(activity)
    if reason is not None:
        return notice(request, title="Registration is closed", text=reason, icon="lock", tone="warning", status=status,
                      link_url=reverse("startups:portal_activities"), link_label="See other open activities")
    focus = public_pages.first_error(public_pages.field_order(form, ("participant_category",)), errors)
    summary = registration_services.public_activity_summary(activity)
    context = {
        "activity": activity,
        "summary": summary,
        "categories": summary["participant_categories"],
        "values": values,
        "errors": errors,
        "message": message,
        "focus": focus,
        "rows": public_pages.answer_rows(form, values["answers"], errors, focus),
    }
    return _render(request, "portal/register.html", f"Register: {activity.title}", context, status=status)


@portal_page_required
@require_GET
def registered(request: HttpRequest, activity_id: int) -> HttpResponse:
    """The success page, only right after registering in this browser (as the public one)."""
    activity = registration_services.registration_page_activity(activity_id)
    if activity is None or not public_pages.has_registered(request, activity.pk):
        return redirect("startups:portal_registrations_page")
    return _render(request, "portal/registered.html", "You're registered", {"activity": activity})


# --- My registrations, certificates, notifications, profile ------------------------------------


@portal_page_required
@require_GET
def my_registrations(request: HttpRequest) -> HttpResponse:
    context = {**pages.registration_rows(request.startup), "activities_url": reverse("startups:portal_activities")}
    return _render(request, "portal/registrations.html", "My registrations", context)


@portal_page_required
@require_GET
def my_certificates(request: HttpRequest) -> HttpResponse:
    """Issued certificates of my registrations only; the download is the permission-checked portal view."""
    certificates = certificates_for_startup(request.startup).select_related("activity").order_by("-issued_at")
    return _render(request, "portal/certificates.html", "My certificates", {"certificates": certificates})


@portal_page_required
@require_GET
def notifications(request: HttpRequest) -> HttpResponse:
    """My notifications (the startup is the recipient), newest first, All or Unread."""
    show = "unread" if request.GET.get("show") == "unread" else "all"
    startup = request.startup
    result = notification_services.list_notifications(startup, request.GET.get("page", "1"), unread_only=show == "unread")
    context = {
        "show": show,
        "result": result,
        "notifications": result.items,
        "unread": notification_services.unread_count(startup),
        "query": "show=unread" if show == "unread" else "",
    }
    return _render(request, "portal/notifications.html", "Notifications", context)


def _notifications_url(request: HttpRequest) -> str:
    target = safe_next(request, reverse("startups:portal_notifications_page"))
    return target if pages.safe_portal_next(target) else reverse("startups:portal_notifications_page")


@portal_page_required
@require_POST
def mark_read(request: HttpRequest, notification_id: int) -> HttpResponse:
    notification_services.mark_read(request.startup, notification_id)  # someone else's: 404
    return redirect(_notifications_url(request))


@portal_page_required
@require_POST
def mark_all_read(request: HttpRequest) -> HttpResponse:
    changed = notification_services.mark_all_read(request.startup)
    messages.success(request, f"{changed} notification{'s' if changed != 1 else ''} marked as read." if changed else "Nothing was unread.")
    return redirect(_notifications_url(request))


@portal_page_required
@require_GET
def profile(request: HttpRequest) -> HttpResponse:
    return _render(request, "portal/profile.html", "Profile", {"startup": request.startup})
