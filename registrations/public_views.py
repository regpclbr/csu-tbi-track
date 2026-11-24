"""The public HTML pages (no login, phase F3): the open activities list, registration, the "Add to calendar"
file, the personal evaluation page and the general evaluation page (one link per activity, which emails
the personal link). Participants open them from email links, often on phones.

Thin views: every rule is in registrations.services (the same functions as the JSON endpoints under /api/);
registrations/public_pages.py parses the posted forms and prepares what the templates show. Plain HTML forms with
CSRF; everything works without JavaScript. The pages never show who registered.
"""

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from activities.models import ActivityStatus
from config.headers import private_token_page

from . import public_pages as pages
from . import services
from .models import EvaluationSubmission, FormType
from .public import guard_evaluation_request, guard_registration_request

NOT_AVAILABLE_TITLE = "This activity is not available"
NOT_AVAILABLE_TEXT = "The link may be wrong, or the activity is not open to the public. See the activities you can register for now."

# Title and icon of each evaluation refusal card; the text is always the service's own reason.
EVALUATION_NOTICES = {
    "invalid": ("This link does not work", "link", "danger"),
    "not_attended": ("Evaluation not available", "user-round", "info"),
    "submitted": ("Evaluation already submitted", "circle-check", "success"),
    "no_form": ("Evaluation not ready yet", "clock", "info"),
    "not_yet_open": ("The evaluation is not open yet", "calendar-clock", "info"),
    "closed": ("The evaluation is closed", "lock", "warning"),
}


def notice(request: HttpRequest, *, title: str, text: str, icon: str, tone: str = "info", status: int = 200, **extra) -> HttpResponse:
    """A friendly card instead of a form (closed registration, refused evaluation link, ...)."""
    context = {"title": title, "text": text, "icon": icon, "tone": tone, **extra}
    return render(request, "public/notice.html", context, status=status)


# --- Open activities --------------------------------------------------------------------------


@require_GET
def open_list(request: HttpRequest) -> HttpResponse:
    """/register/: the activities open for registration now (services.open_activities, the one rule)."""
    cards = pages.open_activity_cards(services.open_activities())
    return render(request, "public/open_list.html", {"cards": cards})


# --- Registration -----------------------------------------------------------------------------


def _registration_context(activity, form, values: dict, errors: dict[str, str]) -> dict:
    focus = pages.first_error(pages.field_order(form, pages.REGISTRATION_FIELDS), errors)
    summary = services.public_activity_summary(activity)
    return {
        "activity": activity,
        "summary": summary,
        "categories": summary["participant_categories"],
        "values": values,
        "errors": errors,
        "focus": focus,
        "rows": pages.answer_rows(form, values.get("answers", {}), errors, focus),
        "honeypot_field": services.HONEYPOT_FIELD,
        "consent_field": services.PRIVACY_CONSENT_FIELD,
    }


@require_GET
def privacy_notice(request: HttpRequest) -> HttpResponse:
    """/privacy/: the privacy notice (ASSUMPTIONS 394). The registration and evaluation pages link here and ask for
    consent; review version (UAT) text of 2026-11-17, final text pending NAVIGATU and the TBI (docs/TBI_QUESTIONS.md)."""
    return render(request, "public/privacy.html")


@require_http_methods(["GET", "POST"])
def register_page(request: HttpRequest, activity_id: int) -> HttpResponse:
    """GET: the activity and the registration form (or why registration is closed). POST: register.

    A POST gets exactly the checks of the JSON endpoint first (guard_registration_request: rate limit per IP
    address and per email, honeypot), then services.register. Draft or unknown: "not available" (404, like the
    API). Ongoing / Conducted: only the title, dates and venue with services.REGISTRATION_ENDED.
    """
    activity = services.registration_page_activity(activity_id)
    if activity is None:
        return notice(request, title=NOT_AVAILABLE_TITLE, text=NOT_AVAILABLE_TEXT, icon="calendar", status=404)
    if activity.status.name != ActivityStatus.SCHEDULED:
        return notice(request, title="Registration has closed", text=services.REGISTRATION_ENDED, icon="lock", ended=activity)

    form = services.get_form(activity, FormType.REGISTRATION)
    values: dict = {"answers": {}}
    errors: dict[str, str] = {}
    message, status = "", 200
    if request.method == "POST":
        post = request.POST
        values = {
            "name": post.get("name", ""),
            "email": post.get("email", ""),
            "contact_number": post.get("contact_number", ""),
            "designation": post.get("designation", ""),
            "office_agency": post.get("office_agency", ""),
            "participant_category": post.get("participant_category", ""),
            "answers": pages.answers_from_post(form, post),
            "privacy_consent": services.has_privacy_consent(post.get(services.PRIVACY_CONSENT_FIELD)),
        }
        try:
            guard_registration_request(request, values["email"], post.get(services.HONEYPOT_FIELD))
            services.check_privacy_consent(post.get(services.PRIVACY_CONSENT_FIELD))
            services.register(
                activity,
                name=values["name"],
                email=values["email"],
                contact_number=values["contact_number"],
                designation=values["designation"],
                office_agency=values["office_agency"],
                participant_category=values["participant_category"] or None,
                answers=values["answers"],
            )
        except services.RegistrationRateLimited as error:
            message, status = str(error), 429
        except services.RegistrationError as error:
            message, status = str(error), 400
        except ValidationError as error:
            errors, status = pages.flat_errors(error.message_dict), 400
        else:
            pages.remember_registration(request, activity.pk)
            return redirect("register_done", activity_id=activity.pk)

    reason = services.registration_closed_reason(activity)
    if reason is not None:  # closed (also when it closed while this person was filling in the form)
        return notice(
            request, title="Registration is closed", text=reason, icon="lock", tone="warning",
            activity=activity, summary=services.public_activity_summary(activity), status=status,
        )
    context = _registration_context(activity, form, values, errors)
    context["message"] = message
    return render(request, "public/register.html", context, status=status)


@require_GET
def register_done(request: HttpRequest, activity_id: int) -> HttpResponse:
    """The success page, only for the browser that just registered (a session flag); anyone else goes to
    the registration page. No personal data: only the activity's public details."""
    activity = services.registration_page_activity(activity_id)
    if activity is None:
        return notice(request, title=NOT_AVAILABLE_TITLE, text=NOT_AVAILABLE_TEXT, icon="calendar", status=404)
    if not pages.has_registered(request, activity.pk):
        return redirect("register", activity_id=activity.pk)
    return render(request, "public/registered.html", {"activity": activity})


@require_GET
def activity_calendar(request: HttpRequest, activity_id: int) -> HttpResponse:
    """GET: the activity as an .ics file ("Add to calendar"). Public facts only."""
    activity = services.registration_page_activity(activity_id)
    if activity is None:
        return notice(request, title=NOT_AVAILABLE_TITLE, text=NOT_AVAILABLE_TEXT, icon="calendar", status=404)
    response = HttpResponse(pages.activity_ics(activity), content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="tbi-activity-{activity.pk}.ics"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


# --- Evaluation (personal signed link) -------------------------------------------------------


def _evaluation_notice(request: HttpRequest, kind: str, reason: str, status: int = 200) -> HttpResponse:
    """A refused link: never the person's name or email, only the service's reason."""
    title, icon, tone = EVALUATION_NOTICES[kind]
    return notice(request, title=title, text=reason, icon=icon, tone=tone, private_page=True, status=status)


def _evaluation_form(request, registration, form, values: dict, errors: dict[str, str], status: int = 200) -> HttpResponse:
    focus = pages.first_error(pages.field_order(form, pages.EVALUATION_FIELDS), errors)
    context = {
        "activity": registration.activity,
        "values": values,
        "errors": errors,
        "focus": focus,
        "rows": pages.answer_rows(form, values["answers"], errors, focus),
        "private_page": True,
    }
    return render(request, "public/evaluate.html", context, status=status)


@private_token_page
@require_http_methods(["GET", "POST"])
def evaluate_page(request: HttpRequest, token: str) -> HttpResponse:
    """The evaluation, in two steps: the form, then a review page ("Your certificate will be issued to ...")
    whose "Confirm and submit" calls services.submit_evaluation, which checks everything again.

    POST step=review: check (services.check_evaluation, nothing saved) and show the review page.
    POST step=edit: back to the form with the values kept. POST step=confirm: submit.
    Every refusal (services.evaluation_state) is a card with the service's reason and no personal data.
    """
    try:
        registration = services.registration_from_token(token)
    except services.RegistrationError as error:
        return _evaluation_notice(request, "invalid", str(error), status=404)
    state = services.evaluation_state(registration)
    if state.kind != "open":
        return _evaluation_notice(request, state.kind, state.reason)

    form = services.get_form(registration.activity, FormType.EVALUATION)
    if request.method == "GET":
        values = {"certificate_name": registration.name, "certificate_email": registration.email, "comments": "", "answers": {}}
        return _evaluation_form(request, registration, form, values, {})

    values = pages.evaluation_values(form, request.POST)
    step = request.POST.get("step")
    if step == "edit":
        return _evaluation_form(request, registration, form, values, {})
    consent_error = ""
    if step == "confirm" and not services.has_privacy_consent(request.POST.get(services.PRIVACY_CONSENT_FIELD)):
        consent_error = services.PRIVACY_CONSENT_REQUIRED  # back to the review page with the message, nothing saved
    elif step == "confirm":
        try:
            services.check_privacy_consent(request.POST.get(services.PRIVACY_CONSENT_FIELD))
            services.submit_evaluation(token, **values)
        except ValidationError as error:
            return _evaluation_form(request, registration, form, values, pages.flat_errors(error.message_dict), status=400)
        except services.RegistrationError as error:  # e.g. submitted meanwhile in another tab
            fresh = services.evaluation_state(registration)
            return _evaluation_notice(request, fresh.kind if fresh.kind != "open" else "closed", str(error))
        return redirect("evaluate_done", token=token)

    try:
        name, email, stored = services.check_evaluation(form, values["certificate_name"], values["certificate_email"], values["answers"])
    except ValidationError as error:
        return _evaluation_form(request, registration, form, values, pages.flat_errors(error.message_dict), status=400)
    context = {
        "activity": registration.activity,
        "certificate_name": name,
        "certificate_email": email,
        "comments": values["comments"].strip(),
        "values": values,
        "hidden_answers": pages.hidden_answers(values["answers"]),
        "review": pages.review_rows(form, stored),
        "private_page": True,
        "consent_field": services.PRIVACY_CONSENT_FIELD,
        "consent_error": consent_error,
    }
    return render(request, "public/evaluate_review.html", context, status=400 if consent_error else 200)


@private_token_page
@require_GET
def evaluate_done(request: HttpRequest, token: str) -> HttpResponse:
    """"Thank you!" after submitting. Shown only when this link's evaluation is submitted (otherwise back to the
    evaluation page); never the name or the email."""
    try:
        registration = services.registration_from_token(token)
    except services.RegistrationError as error:
        return _evaluation_notice(request, "invalid", str(error), status=404)
    if not EvaluationSubmission.objects.filter(registration=registration).exists():
        return redirect("evaluate", token=token)
    return render(request, "public/evaluate_done.html", {"activity": registration.activity, "private_page": True})


# --- General evaluation link (one per activity; emails the personal link) ---------------------------


def _evaluation_request_activity(request: HttpRequest, activity_id: int):
    """The activity, or the "not available" card (404) for Draft and unknown IDs (the registration page's rule)."""
    activity = services.registration_page_activity(activity_id)
    if activity is None:
        return None, notice(request, title=NOT_AVAILABLE_TITLE, text=NOT_AVAILABLE_TEXT, icon="calendar", status=404)
    return activity, None


@require_http_methods(["GET", "POST"])
def evaluation_request_page(request: HttpRequest, activity_id: int) -> HttpResponse:
    """/evaluation/<id>/: the activity's general evaluation link (shared as a QR code). The participant types the
    email they registered with; services.request_evaluation_link emails that registration's personal link when
    they attended and have not evaluated yet. Every accepted request (and a rate-limited or honeypot one) ends on
    the same neutral page, so it never reveals who attended. Before the window opens (or after it closes, or
    without an evaluation form) only the reason is shown. A POST then (a page left open past the deadline) still goes
    through the guard and the service, which records "window_closed" for a registered email so staff can see why
    nothing was sent (ASSUMPTIONS 354); the answer is the same reason card as a GET.
    """
    activity, refusal = _evaluation_request_activity(request, activity_id)
    if refusal:
        return refusal
    state = services.evaluation_request_state(activity)
    if state.kind != "open":
        email = request.POST.get("email", "")
        if request.method == "POST" and guard_evaluation_request(request, email, request.POST.get(services.HONEYPOT_FIELD)):
            try:
                services.request_evaluation_link(activity, email)
            except (ValidationError, services.RegistrationError):
                pass  # always refused while not open; only the record is wanted
        title, icon, tone = EVALUATION_NOTICES["closed" if state.kind == "closed" else "not_yet_open"]
        return notice(request, title=title, text=state.reason, icon=icon, tone=tone, ended=activity)

    values, errors, status = {"email": ""}, {}, 200
    if request.method == "POST":
        values["email"] = request.POST.get("email", "")
        if not guard_evaluation_request(request, values["email"], request.POST.get(services.HONEYPOT_FIELD)):
            return redirect("evaluation_request_sent", activity_id=activity.pk)
        try:
            services.request_evaluation_link(activity, values["email"])
        except ValidationError as error:
            errors, status = pages.flat_errors(error.message_dict), 400
        except services.RegistrationError as error:  # the window closed while the page was open
            return notice(request, title=EVALUATION_NOTICES["closed"][0], text=str(error), icon="lock", tone="warning", ended=activity)
        else:
            return redirect("evaluation_request_sent", activity_id=activity.pk)
    context = {"activity": activity, "values": values, "errors": errors, "focus": "email" if errors else "",
               "honeypot_field": services.HONEYPOT_FIELD}
    return render(request, "public/evaluation_request.html", context, status=status)


@require_GET
def evaluation_request_sent(request: HttpRequest, activity_id: int) -> HttpResponse:
    """The same neutral message for everyone (services.EVALUATION_REQUEST_SENT): it reveals nothing about anyone."""
    activity, refusal = _evaluation_request_activity(request, activity_id)
    if refusal:
        return refusal
    return notice(request, title="Check your email", text=services.EVALUATION_REQUEST_SENT, icon="mail", tone="success",
                  ended=activity)
