"""What the Forms, Registrations and Evaluation tabs of the activity page show (phase F2b). Display helpers
only: every rule and number comes from registrations.services and reports (never repeated here)."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.http import QueryDict

from activities.models import Activity
from activities.services import registered_count
from reports import services as report_services

from . import services
from .models import DefaultFormField, FieldType, FormField, FormTemplate, FormType, Registration

REGISTRATIONS_PER_PAGE = 20

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
    return {
        "filters": filters,
        "page": page,
        "page_query": "tab=registrations" + (f"&{filters.query()}" if filters.query() else ""),
        "result": {"page": page.number, "num_pages": page.paginator.num_pages, "total": page.paginator.count},
        "counts": counts(member, activity),
        "categories": list(activity.participant_categories.order_by("name")),
        "attendance_reason": services.attendance_closed_reason(activity),
        "links_reason": services.evaluation_links_closed_reason(activity),
        "link_recipients": services.evaluation_link_recipients(activity).count(),
        "methods": Registration.ConfirmationMethod.choices,
    }


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


def attendance_sheet_rows(activity: Activity) -> list[dict]:
    """The printed attendance sheet: name and category only (no email or contact number), by name."""
    return [
        {"name": registration.name, "category": registration.participant_category.name}
        for registration in services.list_registrations(activity)
    ]


# --- Evaluation tab -----------------------------------------------------------------------------------------


def evaluation_tab(member, activity: Activity) -> dict:
    """reports.services.activity_evaluation (counts and rating averages only: no comments, text or names),
    with each question's bar width (average out of 5)."""
    results = report_services.activity_evaluation(member, activity)
    questions = [{**question, "percent": round(100 * question["average"] / 5) if question["average"] else 0}
                 for question in results["questions"]]
    answers = results["rating_answers"]
    average = results["average_rating"]
    return {
        "results": results, "questions": questions,
        "average_text": f"{average:.1f} / 5" if average is not None else "–",
        "answers_text": f"{answers} rating answer{'' if answers == 1 else 's'}",
    }
