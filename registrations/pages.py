"""What the Forms, Registrations and Evaluation tabs of the activity page show (phase F2b). Display helpers
only: every rule and number comes from registrations.services and reports (never repeated here)."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.http import QueryDict

from accounts.permissions import Permission, has_permission
from activities.formatting import format_datetime
from activities.models import Activity, ActivityStatus
from activities.services import registered_count
from config.mail import prints_emails
from reports import services as report_services

from . import services
from .models import DefaultFormField, EvaluationSubmission, FieldType, FormField, FormTemplate, FormType, Registration

REGISTRATIONS_PER_PAGE = 20
LINK_ROWS_PER_PAGE = 20
DEFAULT_METHOD = Registration.ConfirmationMethod.WET_SIGNATURE  # the "Confirmed by" choice starts here

# Type badges on the Forms tab (FieldType values -> label shown to staff).
FIELD_TYPE_LABELS = {
    FieldType.SHORT_TEXT: "Short text",
    FieldType.LONG_TEXT: "Long text",
    FieldType.RATING: "Rating 1–5",
    FieldType.MULTIPLE_CHOICE: "Multiple choice",
    FieldType.CHECKBOX: "Checkbox",
}
FIELD_TYPE_CHOICES = list(FIELD_TYPE_LABELS.items())
CHOICE_TYPES = [str(value) for value in services.CHOICE_TYPES]


def field_type_label(field_type: str) -> str:
    return FIELD_TYPE_LABELS.get(field_type, field_type)


# --- Forms tab -----------------------------------------------------------------------------------------


def field_rows(form: FormTemplate) -> list[dict]:
    fields = list(form.fields.order_by("sort_order", "field_id"))
    return [
        {
            "field": field,
            "type_label": field_type_label(field.field_type),
            "options": services.parse_options(field.options),
            "is_first": number == 0,
            "is_last": number == len(fields) - 1,
        }
        for number, field in enumerate(fields)
    ]


def default_field_count(activity: Activity, form_type: str) -> int:
    """How many default fields "Create from defaults" will copy (create_form copies them)."""
    return DefaultFormField.objects.filter(activity_type=activity.activity_type, form_type=form_type).count()


def form_card(
    activity: Activity, form_type: str, *, can_edit: bool = False, editing: int | str | None = None, editor: dict | None = None
) -> dict:
    """One card of the Forms tab. `can_edit`: the member has can_create_activity (the card is then
    `editable` while the form has no answers). `editing`: "new" or a field ID whose inline editor is open;
    `editor`: the editor's values and errors after a refused save."""
    form = services.get_form(activity, form_type)
    card = {
        "form_type": form_type,
        "label": FormType(form_type).label,
        "form": form,
        "locked": bool(form) and services.form_is_locked(form),
        "rows": field_rows(form) if form else [],
        "default_count": 0 if form else default_field_count(activity, form_type),
        "is_registration": form_type == FormType.REGISTRATION,
        # ASSUMPTIONS 409: the registration form waits for "Open registration" (activities created with their documents).
        "needs_ready": form_type == FormType.REGISTRATION and services.registration_form_needs_ready(activity, form),
        "ready_at": form.ready_at if form and form_type == FormType.REGISTRATION else None,
        "categories": [category.name for category in activity.participant_categories.order_by("name")],
        "editing": str(editing) if editing is not None else "",
        "editable": False,
        "editor": editor or {},
    }
    card["editable"] = can_edit and bool(form) and not card["locked"]
    if card["editing"] and not card["editor"]:
        field = next((row["field"] for row in card["rows"] if str(row["field"].pk) == card["editing"]), None)
        card["editor"] = editor_values(field)
    return card


def editor_values(field: FormField | None) -> dict:
    """The inline editor's starting values: the field's, or an empty required short-text field."""
    if field is None:
        return {"field_label": "", "field_type": FieldType.SHORT_TEXT, "is_required": True, "options": [], "errors": {}}
    return {
        "field_label": field.field_label, "field_type": field.field_type, "is_required": field.is_required,
        "options": services.parse_options(field.options), "errors": {},
    }


def forms_tab(activity: Activity, can_edit: bool) -> dict:
    return {"cards": [form_card(activity, form_type, can_edit=can_edit) for form_type in (FormType.REGISTRATION, FormType.EVALUATION)]}


def moved_order(field_ids: list[int], field_id: int, direction: str) -> list[int]:
    """The order after moving one field up or down by one place (unchanged at either end)."""
    ids = list(field_ids)
    index = ids.index(field_id)
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(ids):
        ids[index], ids[target] = ids[target], ids[index]
    return ids


# --- Registrations tab --------------------------------------------------------------------------------------


@dataclass
class RegistrationFilters:
    q: str = ""
    attended: bool | None = None
    category: int | None = None
    page: str = "1"

    def query(self) -> str:
        """The filters as a query string for the pager (the tab is added by the template)."""
        params = {"q": self.q, "attended": "" if self.attended is None else str(self.attended).lower(), "category": self.category or ""}
        return urlencode({key: value for key, value in params.items() if value})

    @property
    def any_set(self) -> bool:
        return bool(self.q or self.attended is not None or self.category)


def parse_registration_filters(params) -> RegistrationFilters:
    """?q=, attended=true|false, category=<id>, page=. Unreadable values are ignored (an empty filter)."""
    attended = {"true": True, "false": False}.get((params.get("attended") or "").strip())
    raw_category = (params.get("category") or "").strip()
    return RegistrationFilters(
        q=(params.get("q") or "").strip()[:100],
        attended=attended,
        category=int(raw_category) if raw_category.isdigit() else None,
        page=(params.get("page") or "1").strip(),
    )


def filters_query(raw: str) -> str:
    """A posted copy of the table's query string, re-read (only the known filters survive)."""
    return parse_registration_filters(QueryDict(raw)).query()


def counts(member, activity: Activity) -> dict:
    """Registered / max slots, attended, evaluations (reports.metrics through reports.services)."""
    results = report_services.activity_evaluation(member, activity)
    registered = registered_count(activity)
    return {
        "registered": registered, "max_slots": activity.max_slots, "slots": f"{registered}/{activity.max_slots}",
        "attended": results["attended"],
        "evaluations": results["evaluations"], "response_rate": results["evaluation_response_rate"],
    }


def registrations_tab(member, activity: Activity, params) -> dict:
    filters = parse_registration_filters(params)
    registrations = services.list_registrations(
        activity, attended=filters.attended, participant_category_id=filters.category, search=filters.q
    )
    page = Paginator(registrations, REGISTRATIONS_PER_PAGE).get_page(filters.page)
    rows = [{"registration": r, "undo_reason": services.undo_refused_reason(r)} for r in page.object_list]
    return {
        "rows": rows,
        "filters": filters,
        "page": page,
        "page_query": "tab=registrations" + (f"&{filters.query()}" if filters.query() else ""),
        "result": {"page": page.number, "num_pages": page.paginator.num_pages, "total": page.paginator.count},
        "counts": counts(member, activity),
        "categories": list(activity.participant_categories.order_by("name")),
        "attendance_reason": services.attendance_closed_reason(activity),
        "methods": _methods(),
        "default_method": DEFAULT_METHOD,
    }


def _methods() -> list[tuple[str, str]]:
    """The "Confirmed by" choices, wet signature first (the default)."""
    choices = dict(Registration.ConfirmationMethod.choices)
    return [(DEFAULT_METHOD, choices.pop(DEFAULT_METHOD)), *choices.items()]


def answer_rows(registration: Registration) -> list[tuple[str, str]]:
    """A registration's answers as (label, readable value), in the form's order; checkbox answers joined."""
    answers = sorted(registration.answers.all(), key=lambda answer: (answer.field.sort_order, answer.field_id))
    rows = []
    for answer in answers:
        value = answer.answer_value
        if answer.field.field_type == FieldType.CHECKBOX:
            value = ", ".join(services.parse_options(value))
        rows.append((answer.field.field_label, value))
    return rows


def evaluation_detail(member, registration: Registration) -> dict | None:
    """The registration dialog's "Evaluation answers" (ASSUMPTIONS 402): services.evaluation_answers for staff who
    may see individual evaluations, else None (the dialog then shows a note; the summary has no names)."""
    if not services.can_view_individual_evaluations(member):
        return None
    try:
        return services.evaluation_answers(member, registration)
    except EvaluationSubmission.DoesNotExist:
        return None


def attendance_sheet_rows(activity: Activity) -> list[dict]:
    """The printed attendance sheet: name and category only (no email or contact number), by name."""
    return [
        {"name": registration.name, "category": registration.participant_category.name}
        for registration in services.list_registrations(activity)
    ]


# --- Evaluation tab -----------------------------------------------------------------------------------------


def link_explanation(activity: Activity) -> list[str]:
    """How evaluation links work for this activity, from the rules themselves (registrations.services)."""
    opens, closes = services.evaluation_window(activity)
    lines = [
        "Who gets the link: everyone marked as attended who has not answered the evaluation yet.",
        f"It is emailed automatically, once per person, after the activity becomes Conducted (checked every "
        f"{settings.ACTIVITY_STATUS_INTERVAL_MINUTES} minutes while qcluster is running). A failed email is not "
        "retried automatically: resend it below.",
        f"The evaluation opens on {format_datetime(opens)} and closes on {format_datetime(closes)}.",
    ]
    if services.get_form(activity, FormType.EVALUATION) is None:
        lines.append("There is no evaluation form yet: create it on the Forms tab. No link is sent without it.")
    return lines


def link_state(registration: Registration) -> dict:
    """One attendee's link: sent (when; "Printed only" when the email was only printed in the terminal), failed
    (when) or not sent yet."""
    if registration.evaluation_link_failed_at:
        return {"label": "Failed", "tone": "danger", "at": registration.evaluation_link_failed_at}
    if registration.evaluation_link_sent_at:
        printed = registration.evaluation_link_delivery in PRINTED
        return {"label": "Printed only" if printed else "Sent", "tone": "warning" if printed else "success",
                "at": registration.evaluation_link_sent_at}
    return {"label": "Not sent yet", "tone": "neutral", "at": None}


Delivery, RequestResult = Registration.LinkDelivery, Registration.LinkRequestResult
PRINTED = (Delivery.PRINTED_DEV, Delivery.PRINTED_DEMO)
LINK_REQUEST_ROWS = 20
# Why a request sent nothing (Registration.evaluation_link_request_result).
REQUEST_NOT_SENT = {
    RequestResult.NOT_ATTENDED: "not marked as attended",
    RequestResult.ALREADY_EVALUATED: "already evaluated",
    RequestResult.WINDOW_CLOSED: "the evaluation was not open",
}
DELIVERY_NOT_SENT = {
    Delivery.PRINTED_DEV: "only printed in the qcluster terminal (development mode)",
    Delivery.PRINTED_DEMO: "demo address, only printed in the terminal",
}


def link_request_outcome(registration: Registration) -> dict:
    """The latest general-link request of one registration and what came of it (ASSUMPTIONS 354), from
    evaluation_link_request_result and, for a queued request, the delivery recorded after it
    (evaluation_link_sent_at / _failed_at / _delivery, set by services.deliver_evaluation_link):
    {"sent": bool, "text": "Sent" | "Not sent: <reason>" | "Queued: waiting for qcluster"}."""
    result, requested = registration.evaluation_link_request_result, registration.evaluation_link_requested_at
    if result in REQUEST_NOT_SENT:
        return {"sent": False, "pending": False, "text": f"Not sent: {REQUEST_NOT_SENT[result]}"}
    failed, sent = registration.evaluation_link_failed_at, registration.evaluation_link_sent_at
    if failed and failed >= requested:
        return {"sent": False, "pending": False, "text": "Not sent: the email failed (resend it below)"}
    if sent and sent >= requested:
        if registration.evaluation_link_delivery in DELIVERY_NOT_SENT:
            return {"sent": False, "pending": False, "text": f"Not sent: {DELIVERY_NOT_SENT[registration.evaluation_link_delivery]}"}
        return {"sent": True, "pending": False, "text": "Sent"}
    return {"sent": False, "pending": True, "text": "Queued: waiting for qcluster to send it"}


def link_requests(activity: Activity) -> list[dict]:
    """The Evaluation tab's "Link requests" list: the registrations of this activity whose email was typed on the
    general evaluation page, the latest request first (LINK_REQUEST_ROWS). Only registrations: an email that matches
    nobody is never stored. Staff already see who registered, attended and evaluated (Registrations tab)."""
    rows = activity.registrations.filter(evaluation_link_requested_at__isnull=False).order_by(
        "-evaluation_link_requested_at", "-registration_id")[:LINK_REQUEST_ROWS]
    return [{"registration": r, "requested_at": r.evaluation_link_requested_at, **link_request_outcome(r)} for r in rows]


def evaluation_links(member, activity: Activity, params) -> dict:
    """The "Evaluation links" card: the explanation, the counts, and the attendees who have not answered
    (20 a page) with their link state. Resend needs can_create_activity and an open window
    (services.evaluation_links_closed_reason, the rule send_evaluation_links checks)."""
    recipients = services.evaluation_link_recipients(activity).order_by("name", "registration_id")
    page = Paginator(recipients, LINK_ROWS_PER_PAGE).get_page((params.get("page") or "1").strip())
    return {
        "explanation": link_explanation(activity),
        "console_mode": prints_emails(),
        "link_counts": services.evaluation_link_counts(activity),
        "link_requests": link_requests(activity),
        "link_rows": [{"registration": r, "state": link_state(r)} for r in page.object_list],
        "link_page": page,
        "link_result": {"page": page.number, "num_pages": page.paginator.num_pages, "total": page.paginator.count},
        "can_resend": has_permission(member, Permission.CREATE_ACTIVITY),
        "resend_reason": services.evaluation_links_closed_reason(activity),
        "is_conducted": activity.status.name == ActivityStatus.CONDUCTED,
    }


def evaluation_tab(member, activity: Activity, params=None) -> dict:
    """reports.services.activity_evaluation (counts and rating averages only: no comments, text or names of
    people who answered), with each question's bar width (average out of 5), plus the evaluation links card."""
    results = report_services.activity_evaluation(member, activity)
    questions = [{**question, "percent": round(100 * question["average"] / 5) if question["average"] else 0}
                 for question in results["questions"]]
    answers = results["rating_answers"]
    average = results["average_rating"]
    return {
        "results": results, "questions": questions,
        "average_text": f"{average:.1f} / 5" if average is not None else "–",
        "answers_text": f"{answers} rating answer{'' if answers == 1 else 's'}",
        **evaluation_links(member, activity, params or {}),
    }
