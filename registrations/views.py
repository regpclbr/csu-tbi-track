"""JSON endpoints for forms, public registration, attendance and evaluations.

Views are thin: parse input, check permission, call registrations.services.
Public JSON endpoints (no login): /api/register/<activity_id>/ and /api/evaluate/<token>/.
(/register/<id>/ and /evaluate/<token>/ are the HTML pages, registrations/public_views.py; emails link there.)
Staff endpoints: /registrations/api/... (reading needs staff login; changes need can_create_activity).
"""

import json
from collections.abc import Callable
from functools import wraps

from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from config.json import JsonResponse  # date-times in Manila time

from accounts.permissions import Permission, permission_required, staff_login_required
from activities.models import Activity
from config.parsing import parse_bool

from . import services, signatures
from .public import guard_registration_request
from .models import FormField, FormTemplate


class BadRequest(Exception):
    """Invalid request body or query parameters."""


def api_errors(view: Callable) -> Callable:
    """Turn refusals and validation errors into 400 responses with clear messages."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> JsonResponse:
        try:
            return view(request, *args, **kwargs)
        except ValidationError as error:
            errors = error.message_dict if hasattr(error, "error_dict") else {"__all__": error.messages}
            return JsonResponse({"errors": errors}, status=400)
        except services.RegistrationRateLimited as error:
            return JsonResponse({"error": str(error)}, status=429)
        except (services.RegistrationError, BadRequest) as error:
            return JsonResponse({"error": str(error)}, status=400)

    return wrapper


def _json_body(request: HttpRequest) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError as error:
        raise BadRequest("Request body must be valid JSON.") from error
    if not isinstance(data, dict):
        raise BadRequest("Request body must be a JSON object.")
    return data


# --- Public: registration ---------------------------------------------------------------


@require_GET
def public_open_activities(request: HttpRequest) -> JsonResponse:
    """GET: activities open for registration now (soonest first), with the public fields of the
    registration page (no form, never who registered). Open each with /api/register/<id>/."""
    return JsonResponse({"activities": [services.public_activity_summary(a) for a in services.open_activities()]})


@require_http_methods(["GET", "POST"])
@api_errors
def public_register(request: HttpRequest, activity_id: int) -> JsonResponse:
    """GET: activity info, categories, slots left and the form. POST: register.

    POST {"name", "email", "contact_number", "designation", "office_agency" (both required, ASSUMPTIONS 426),
          "participant_category": id (optional if only one),
          "answers": {"<field_id>": value}, "website": "" (the hidden honeypot field; leave empty),
          "privacy_consent": true (required: the person agreed to the privacy notice, /privacy/),
          "signature": {"kind": "draw" | "upload" | "typed", "image": "data:image/png;base64,…" (draw / upload: PNG or
                        JPG), "typed_name": "…", "confirmed": true (typed)} (required, ASSUMPTIONS 434)}
    Too many attempts from one IP address or for one email: 429.
    """
    activity = services.get_public_activity(activity_id)
    if activity is None:
        raise Http404("Activity not found.")
    if request.method == "GET":
        return JsonResponse({"activity": services.public_activity_info(activity)})

    data = _json_body(request)
    guard_registration_request(request, data.get("email"), data.get(services.HONEYPOT_FIELD))
    services.check_privacy_consent(data.get(services.PRIVACY_CONSENT_FIELD))
    registration = services.register(
        activity,
        name=str(data.get("name", "")),
        email=str(data.get("email", "")),
        contact_number=str(data.get("contact_number", "")),
        designation=str(data.get("designation", "")),
        office_agency=str(data.get("office_agency", "")),
        participant_category=data.get("participant_category"),
        answers=data.get("answers") or {},
        signature=signatures.from_json(data.get("signature")),
    )
    return JsonResponse(
        {
            "message": "You are registered. A confirmation email is on its way.",
            "participant_category": registration.participant_category.name,
            "slots_remaining": services.slots_remaining(activity),
        },
        status=201,
    )


# --- Public: evaluation (personal signed link) --------------------------------------------


@require_http_methods(["GET", "POST"])
@api_errors
def public_evaluate(request: HttpRequest, token: str) -> JsonResponse:
    """GET: the evaluation form (or why it is closed), with the suggested certificate name and email.

    POST {"certificate_name", "certificate_email", "comments", "answers": {...}, "privacy_consent": true}: submit.
    """
    if request.method == "GET":
        return JsonResponse({"evaluation": services.evaluation_page(token)})
    data = _json_body(request)
    services.check_privacy_consent(data.get(services.PRIVACY_CONSENT_FIELD))
    services.submit_evaluation(
        token,
        certificate_name=str(data.get("certificate_name", "")),
        certificate_email=str(data.get("certificate_email", "")),
        comments=str(data.get("comments", "")),
        answers=data.get("answers") or {},
    )
    return JsonResponse({"message": "Thank you! Your evaluation was submitted."}, status=201)


# --- Staff: forms --------------------------------------------------------------------------


def _form_json(form: FormTemplate) -> dict:
    return {
        "template_id": form.pk,
        "activity_id": form.activity_id,
        "form_type": form.form_type,
        "title": form.title,
        "generated_from_default": form.generated_from_default,
        "locked": services.form_is_locked(form),
        "fields": [services._field_json(field) for field in form.fields.order_by("sort_order", "field_id")],
    }


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def create_form_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """POST {"form_type": "registration" | "evaluation", "title": optional}"""
    activity = get_object_or_404(Activity, pk=activity_id)
    data = _json_body(request)
    form = services.create_form(request.user, activity, str(data.get("form_type", "")), str(data.get("title", "")))
    return JsonResponse({"form": _form_json(form)}, status=201)


@staff_login_required
@require_GET
def form_api(request: HttpRequest, template_id: int) -> JsonResponse:
    return JsonResponse({"form": _form_json(get_object_or_404(FormTemplate, pk=template_id))})


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def add_field_api(request: HttpRequest, template_id: int) -> JsonResponse:
    """POST {"field_label", "field_type", "options": [..], "is_required"}"""
    form = get_object_or_404(FormTemplate, pk=template_id)
    data = _json_body(request)
    services.add_field(
        request.user,
        form,
        field_label=str(data.get("field_label", "")),
        field_type=str(data.get("field_type", "")),
        options=data.get("options"),
        is_required=data.get("is_required", True),
    )
    return JsonResponse({"form": _form_json(form)}, status=201)


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def update_field_api(request: HttpRequest, field_id: int) -> JsonResponse:
    """POST only what changes: field_label, field_type, options, is_required."""
    field = get_object_or_404(FormField, pk=field_id)
    services.update_field(request.user, field, **_json_body(request))
    return JsonResponse({"form": _form_json(field.template)})


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def remove_field_api(request: HttpRequest, field_id: int) -> JsonResponse:
    field = get_object_or_404(FormField, pk=field_id)
    form = field.template
    services.remove_field(request.user, field)
    return JsonResponse({"form": _form_json(form)})


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def reorder_fields_api(request: HttpRequest, template_id: int) -> JsonResponse:
    """POST {"field_ids": [3, 1, 2]}"""
    form = get_object_or_404(FormTemplate, pk=template_id)
    field_ids = _json_body(request).get("field_ids")
    if not isinstance(field_ids, list):
        raise BadRequest("field_ids must be a list.")
    services.reorder_fields(request.user, form, field_ids)
    return JsonResponse({"form": _form_json(form)})


# --- Staff: registrations and attendance -----------------------------------------------


@staff_login_required
@require_GET
@api_errors
def registrations_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """GET ?attended=true|false&category=<id>"""
    activity = get_object_or_404(Activity, pk=activity_id)
    attended = parse_bool(request.GET.get("attended"), "attended")
    category = request.GET.get("category") or None
    if category is not None and not category.isdigit():
        raise BadRequest("category must be a number.")
    rows = [
        {
            "registration_id": r.pk,
            "name": r.name,
            "email": r.email,
            "contact_number": r.contact_number,
            "designation": r.designation,
            "office_agency": r.office_agency,
            "participant_category": r.participant_category.name if r.participant_category else None,
            "registration_date": r.registration_date,
            "attendance_confirmed": r.attendance_confirmed,
            "attendance_days": [{"day": d.day, "method": d.method, "checked_in_at": d.checked_in_at}
                                for d in r.attendance_days.all()],
            "registration_source": r.registration_source,
            "cancelled": r.cancelled_at is not None,
            "evaluation_submitted": hasattr(r, "evaluation_submission"),
            "answers": [{"field_label": a.field.field_label, "answer_value": a.answer_value} for a in r.answers.all()],
        }
        for r in services.list_registrations(activity, attended, int(category) if category else None)
    ]
    return JsonResponse({"registrations": rows, "total": len(rows)})


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def attendance_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """POST {"registration_ids": [..], "attended": true|false, "method": "email" | "wet_signature"}"""
    activity = get_object_or_404(Activity, pk=activity_id)
    data = _json_body(request)
    ids = data.get("registration_ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise BadRequest("registration_ids must be a list of numbers.")
    attended = parse_bool(data.get("attended"), "attended")
    if attended is None:
        raise BadRequest("attended is required: true or false.")
    changed = services.set_attendance(request.user, activity, ids, attended, data.get("method"))
    return JsonResponse({"changed": changed})


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def send_evaluation_links_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """Queues one email per attendee who has not evaluated yet; {"queued": n} (sent in the background)."""
    activity = get_object_or_404(Activity, pk=activity_id)
    return JsonResponse({"queued": services.send_evaluation_links(request.user, activity)})
