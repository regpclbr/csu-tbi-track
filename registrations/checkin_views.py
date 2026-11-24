"""The public QR attendance check-in page and the "I can no longer attend" page (ASSUMPTIONS 434). No login; phones first.

/checkin/<id>/ (the venue's QR code), one page in steps, every POST through attendance.guard_checkin (rate limit per IP
and per email, honeypot):
- step "email": the participant types the email they registered with -> checked in / "Already checked in today at …" /
  the signature step (registered, no signature yet) / the walk-in form (not registered);
- step "signature": draw, upload or type the signature + privacy consent, then checked in;
- step "walkin": name, email, contact number, category, Designation / Position, Office / Agency, signature and privacy
  consent -> registered as a walk-in and checked in.
After a check-in the browser goes to /checkin/<id>/done/ (a session flag; no personal data, only the day and the time).

/registration/cancel/<token>/: the confirmation email's signed link. GET only shows the page; a POST cancels or restores.

Thin views: every rule is in registrations.attendance / services / signatures.
"""

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from activities.services import refresh_status
from config.headers import private_token_page
from config.network import client_ip

from . import attendance, services, signatures
from . import public_pages as pages
from .public_views import NOT_AVAILABLE_TEXT, NOT_AVAILABLE_TITLE, notice

DONE_SESSION_KEY = "checkin_done"
STEPS = ("email", "signature", "walkin")


def _activity(activity_id: int):
    activity = services.registration_page_activity(activity_id)
    if activity is not None:
        refresh_status(activity)  # a started activity becomes Ongoing first (the check-in window needs no status move)
    return activity


def _context(activity, step: str, values: dict, errors: dict, message: str = "") -> dict:
    summary = services.public_activity_summary(activity)
    return {
        "activity": activity, "summary": summary, "categories": summary["participant_categories"], "step": step,
        "values": values, "errors": errors, "message": message,
        "focus": next((key for key in (*pages.REGISTRATION_FIELDS, signatures.FIELD, services.PRIVACY_CONSENT_FIELD)
                       if key in errors), None),
        "honeypot_field": services.HONEYPOT_FIELD, "consent_field": services.PRIVACY_CONSENT_FIELD,
    }


def _done(request: HttpRequest, activity, result: attendance.CheckinResult) -> HttpResponse:
    request.session[DONE_SESSION_KEY] = {"activity_id": activity.pk, "text": result.message}
    return redirect("checkin_done", activity_id=activity.pk)


@require_http_methods(["GET", "POST"])
def checkin_page(request: HttpRequest, activity_id: int) -> HttpResponse:
    activity = _activity(activity_id)
    if activity is None:
        return notice(request, title=NOT_AVAILABLE_TITLE, text=NOT_AVAILABLE_TEXT, icon="calendar", status=404)
    state = attendance.checkin_state(activity)
    if state.kind != "open":
        title = "Check-in is not open yet" if state.kind == "not_yet_open" else "Check-in is closed"
        return notice(request, title=title, text=state.reason, icon="clock", tone="info", ended=activity)
    if request.method == "GET":
        return render(request, "public/checkin.html", _context(activity, "email", {}, {}))

    post = request.POST
    step = post.get("step") if post.get("step") in STEPS else "email"
    values = {key: post.get(key, "") for key in ("email", *pages.REGISTRATION_FIELDS)}
    values["privacy_consent"] = services.has_privacy_consent(post.get(services.PRIVACY_CONSENT_FIELD))
    values["signature_kind"] = post.get("signature_kind", "")
    values["signature_typed_name"] = post.get("signature_typed_name", "")
    signature = signatures.from_post(post, request.FILES)
    try:
        attendance.guard_checkin(client_ip(request), values["email"], post.get(services.HONEYPOT_FIELD))
        if step == "walkin":
            services.check_privacy_consent(post.get(services.PRIVACY_CONSENT_FIELD))
            result = attendance.register_walk_in(
                activity, name=values["name"], email=values["email"], contact_number=values["contact_number"],
                designation=values["designation"], office_agency=values["office_agency"],
                participant_category=values["participant_category"] or None, signature=signature)
        else:
            if step == "signature":
                services.check_privacy_consent(post.get(services.PRIVACY_CONSENT_FIELD))
                if not signatures.is_given(signature):  # nothing chosen: the same required message as everywhere
                    signatures.clean_signature(signature)
            result = attendance.check_in(activity, values["email"], signature=signature if step == "signature" else None)
    except services.RegistrationRateLimited as error:
        return render(request, "public/checkin.html", _context(activity, step, values, {}, str(error)), status=429)
    except (attendance.AttendanceError, services.RegistrationError) as error:
        return render(request, "public/checkin.html", _context(activity, step, values, {}, str(error)), status=400)
    except ValidationError as error:
        return render(request, "public/checkin.html", _context(activity, step, values, pages.flat_errors(error.message_dict)),
                      status=400)

    if result.kind in ("checked_in", "already"):
        return _done(request, activity, result)
    if result.kind == "needs_signature":
        return render(request, "public/checkin.html", _context(activity, "signature", values, {}))
    values = {**values, "privacy_consent": False}  # not_registered: the walk-in form, with the typed email kept
    return render(request, "public/checkin.html", _context(activity, "walkin", values, {}))


@require_GET
def checkin_done(request: HttpRequest, activity_id: int) -> HttpResponse:
    """"You're checked in" (or "Already checked in today at …"), only for the browser that just did it."""
    activity = services.registration_page_activity(activity_id)
    done = request.session.get(DONE_SESSION_KEY) or {}
    if activity is None or done.get("activity_id") != activity_id:
        return redirect("checkin", activity_id=activity_id)
    return render(request, "public/checkin_done.html", {"activity": activity, "text": done.get("text", "")})


# --- "I can no longer attend" ------------------------------------------------------------------------------------------


@private_token_page
@require_http_methods(["GET", "POST"])
def cancel_page(request: HttpRequest, token: str) -> HttpResponse:
    """GET: the activity and one button ("I can no longer attend", or "I can attend after all" once cancelled). Only a
    POST changes anything (action=cancel|restore). Never shows the name or the email."""
    try:
        registration = services.registration_from_cancel_token(token)
    except services.RegistrationError as error:
        return notice(request, title="This link does not work", text=str(error), icon="link", tone="danger",
                      private_page=True, status=404)
    message, status = "", 200
    if request.method == "POST":
        try:
            if request.POST.get("action") == "restore":
                registration = services.restore_registration(registration)
                message = "You're registered again. See you there!"
            else:
                registration = services.cancel_registration(registration)
                message = "Thank you for telling us. Your slot was freed."
        except services.RegistrationError as error:
            message, status = str(error), 400
    state = services.cancellation_state(registration)
    return render(request, "public/cancel.html", {
        "activity": registration.activity, "state": state, "message": message, "private_page": True,
    }, status=status)
