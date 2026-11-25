"""What the Activities pages show (activities/page_views.py). Display helpers only: reading the query
string, the month grid, the tabs and the readable detail rows. Every rule and number comes from
activities.services and registrations.services (never repeated here)."""

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import Permission, has_permission
from registrations import services as registration_services
from registrations.models import FormType
from reports.dashboard_page import DONUT_STROKE, category_color, donut_segments
from reports.metrics import shares

from . import attention, services
from .formatting import audit_sentence, format_date, format_datetime, format_money
from .models import Activity, ActivityStatus

LIST, CALENDAR = "list", "calendar"

# The page's sort options: "Grouped" (the default, services.grouped_activities) and the plain orders of
# services.list_activities (SORT_OPTIONS), with readable labels (a test checks they match).
SORT_CHOICES = [
    (services.GROUPED_SORT, "Grouped (recommended)"),
    ("-start_date", "Start date: newest first"),
    ("start_date", "Start date: oldest first"),
    ("title", "Title: A to Z"),
    ("-title", "Title: Z to A"),
    ("-registered", "Most registered"),
    ("registered", "Fewest registered"),
    ("-created_at", "Recently created"),
    ("created_at", "First created"),
]

# Tabs of the activity page: (key, label).
TABS = [
    ("overview", "Overview"),
    ("history", "History"),
    ("forms", "Forms"),
    ("registrations", "Registrations"),
    ("evaluation", "Evaluation"),
    ("documents", "Documents"),
    ("certificates", "Certificates"),
]
DEFAULT_TAB = "overview"
NO_EVALUATION_DEADLINE = "No deadline (the evaluation stays open)"  # ASSUMPTIONS 423

# Shown to staff instead of the public wording when only the registration form is missing (owner request, F2a).
FORM_MISSING_NOTICE = "Registration form not created yet. Create it in the Forms tab (phase F2b)."
# ASSUMPTIONS 409: the form exists but is not marked ready (activities created with their documents).
FORM_NOT_READY_NOTICE = "Registration stays closed until you check the registration form and click “Open registration” on the Forms tab."


@dataclass
class ListFilters:
    """The Activities list's query string, checked. Bad values are dropped and explained in `errors`."""

    q: str = ""
    activity_type: int | None = None
    status: int | None = None
    participant_category: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    sort: str = services.GROUPED_SORT
    preset: str = ""  # one of services.LIST_PRESETS (a list card), or ""
    page: str = "1"
    view: str = LIST
    month: date = field(default_factory=lambda: timezone.localdate().replace(day=1))
    errors: list[str] = field(default_factory=list)

    def for_service(self) -> dict:
        """The filters in the form services.list_activities() takes."""
        return {
            "search": self.q, "activity_type": self.activity_type, "status": self.status,
            "participant_category": self.participant_category, "date_from": self.date_from,
            "date_to": self.date_to, "sort": self.sort, "preset": self.preset,
        }

    def query(self, **changes) -> str:
        """The URL query for these filters (without empty values), e.g. to keep them in a link."""
        values = replace(self, **changes) if changes else self
        params = {
            "q": values.q, "activity_type": values.activity_type or "", "status": values.status or "",
            "participant_category": values.participant_category or "",
            "date_from": values.date_from.isoformat() if values.date_from else "",
            "date_to": values.date_to.isoformat() if values.date_to else "",
            "sort": "" if values.sort == services.GROUPED_SORT else values.sort,
            "preset": values.preset,
            "view": "" if values.view == LIST else values.view,
            "month": values.month.strftime("%Y-%m") if values.view == CALENDAR else "",
        }
        return urlencode({key: value for key, value in params.items() if value not in ("", None)})

    @property
    def grouped(self) -> bool:
        return self.sort == services.GROUPED_SORT

    @property
    def any_set(self) -> bool:
        return bool(
            self.q or self.activity_type or self.status or self.participant_category or self.date_from or self.date_to
            or self.preset
        )

    @property
    def preset_label(self) -> str:
        return services.LIST_PRESETS.get(self.preset, "")


def _int(params, name: str, errors: list[str], label: str) -> int | None:
    raw = (params.get(name) or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    errors.append(f"Ignored the {label} filter: it is not valid.")
    return None


def _date(params, name: str, errors: list[str], label: str) -> date | None:
    raw = (params.get(name) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors.append(f"Ignored the {label} date: use the date picker (YYYY-MM-DD).")
        return None


def _preset(params, filters: ListFilters, errors: list[str]) -> str:
    preset = (params.get("preset") or "").strip()
    if not preset:
        return ""
    if preset not in services.LIST_PRESETS:
        errors.append("Ignored an unknown list shortcut.")
        return ""
    if filters.status or (preset == "conducted_month" and (filters.date_from or filters.date_to)):
        return ""
    return preset


def parse_list_filters(params, today: date | None = None) -> ListFilters:
    """Read ?q, activity_type, status, participant_category, date_from, date_to, sort, preset, page, view, month.

    A preset (a list card) is dropped when another filter contradicts it: any status filter (each preset has its
    own status rule), and From / To dates for "Conducted this month" (it has its own date rule). Search, type,
    category and sort combine with every preset."""
    today = today or timezone.localdate()
    errors: list[str] = []
    filters = ListFilters(
        q=(params.get("q") or "").strip()[:100],
        activity_type=_int(params, "activity_type", errors, "activity type"),
        status=_int(params, "status", errors, "status"),
        participant_category=_int(params, "participant_category", errors, "participant category"),
        date_from=_date(params, "date_from", errors, "From"),
        date_to=_date(params, "date_to", errors, "To"),
        page=(params.get("page") or "1").strip(),
        view=CALENDAR if params.get("view") == CALENDAR else LIST,
        month=today.replace(day=1),
        errors=errors,
    )
    sort = params.get("sort") or services.GROUPED_SORT
    if sort == services.GROUPED_SORT or sort in services.SORT_OPTIONS:
        filters.sort = sort
    else:
        errors.append("Ignored an unknown sort order.")
    if filters.date_from and filters.date_to and filters.date_to < filters.date_from:
        errors.append("Ignored the dates: “To” is before “From”.")
        filters.date_from = filters.date_to = None
    filters.preset = _preset(params, filters, errors)
    if month := (params.get("month") or "").strip():
        try:
            filters.month = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            errors.append("Ignored the month: use YYYY-MM.")
    return filters


# The Activities list cards: (preset, stats key, label, icon, chip colour, pill). Preset "" = "Activities in total"
# (a plain card); the others open a dialog with their activities (ASSUMPTIONS 341).
STAT_CARDS = [
    ("", "total", "Activities in total", "layout-grid", "blue", ""),
    ("open", "open_for_registration", services.LIST_PRESETS["open"], "user-plus", "green", "open now"),
    ("conducted_month", "conducted_this_month", services.LIST_PRESETS["conducted_month"], "circle-check", "teal", ""),
    ("conducted", "conducted_total", services.LIST_PRESETS["conducted"], "award", "violet", ""),
]

# The dialog's empty state for each preset.
PRESET_EMPTY = {
    "open": "No activities are open for registration right now.",
    "conducted_month": "No activities were conducted this month.",
    "conducted": "No activities have been conducted yet.",
}


def preset_list_url(preset: str) -> str:
    """The Activities list with a card's preset (the dialog's "Show all in the list", and the no-JavaScript card)."""
    return f"{reverse('activities:list')}?{urlencode({'preset': preset})}"


def stat_cards(stats: dict[str, int]) -> list[dict]:
    """The four cards. A card with a preset opens its dialog (HTMX: `dialog_url`); without JavaScript it is a link to
    the list with the preset (`list_url`). They never filter the list below."""
    return [
        {
            "preset": preset, "label": label, "value": stats[key], "icon": icon, "chip": chip, "pill": pill,
            "dialog_url": reverse("activities:card_dialog", args=[preset]) if preset else "",
            "list_url": preset_list_url(preset) if preset else "",
        }
        for preset, key, label, icon, chip, pill in STAT_CARDS
    ]


def card_dialog(preset: str, now: datetime | None = None) -> dict:
    """What a card's dialog shows: its title with the count, up to services.PRESET_LIST_LIMIT rows (the card's own
    query, services.preset_list) and, when there are more, the link to the whole list with the preset."""
    count, activities = services.preset_list(preset, limit=services.PRESET_LIST_LIMIT, now=now)
    return {
        "preset": preset,
        "title": f"{services.LIST_PRESETS[preset]} ({count:,})",
        "count": count,
        "rows": list_rows(activities),
        "more": count > len(activities),
        "limit": services.PRESET_LIST_LIMIT,
        "empty": PRESET_EMPTY[preset],
        "list_url": preset_list_url(preset),
    }


# --- List rows and calendar ---------------------------------------------------------------------


def _percent(registered: int, max_slots: int) -> int:
    return min(100, round(100 * registered / max_slots)) if max_slots else 0


def list_rows(activities: list[Activity], now: datetime | None = None) -> list[dict]:
    """Rows of the list: the activity, its categories, registered/max (`registered` comes from
    services.list_activities) and how many things need attention (activities.attention, fixed number of queries).
    `note`: the attention item about attendance or a late Draft (the "Needs follow-up" cards show it)."""
    items = attention.attention_items(activities, now)
    rows = []
    for activity in activities:
        own = items.get(activity.pk, [])
        rows.append({
            "activity": activity,
            "attention": attention.warning_count(own),  # informational items are not counted (ASSUMPTIONS 364)
            "note": next((item.text for item in own if item.kind in ("draft", "attendance")), ""),
            "categories": sorted(category.name for category in activity.participant_categories.all()),
            "registered": activity.registered,
            "percent": _percent(activity.registered, activity.max_slots),
        })
    return rows


def _follow_up_actions(activity: Activity, can_create: bool) -> dict:
    """The quick actions of a "Needs follow-up" card. Ongoing (its end has passed): "Mark attendance" (the
    Registrations tab) and "Mark as Conducted" when services.allowed_next_statuses() offers it; both need
    can_create_activity (the service and the views check it again). Draft: only "Open activity"."""
    ongoing = activity.status.name == ActivityStatus.ONGOING
    return {
        "mark_attendance": ongoing and can_create,
        "mark_conducted": ongoing and can_create and ActivityStatus.CONDUCTED in services.allowed_next_statuses(activity),
    }


def grouped_sections(groups: list[services.ActivityGroup], can_create: bool, now: datetime | None = None) -> list[dict]:
    """The grouped list (services.grouped_activities) ready to show: one attention load for every section, the
    follow-up cards' actions, and Past split under month headings ("October 2026", by start date)."""
    rows = iter(list_rows([activity for group in groups for activity in group.items], now))
    sections = []
    for group in groups:
        section_rows = [next(rows) for _ in group.items]
        if group.key == services.FOLLOW_UP:
            for row in section_rows:
                row["actions"] = _follow_up_actions(row["activity"], can_create)
        months = []
        if group.key == services.PAST:
            for row in section_rows:
                label = row["activity"].start_date.strftime("%B %Y")
                if not months or months[-1]["label"] != label:
                    months.append({"label": label, "rows": []})
                months[-1]["rows"].append(row)
        sections.append({"key": group.key, "label": group.label, "total": group.total, "rows": section_rows,
                         "months": months, "page": group.page})
    return sections


CALENDAR_CHIPS_PER_DAY = 3


def month_grid(month: date, today: date | None = None) -> dict:
    """The calendar month (Monday first): weeks of days, each with the activities on that day
    (services.activities_between, every status). A multi-day activity is on each of its days."""
    today = today or timezone.localdate()
    weeks = calendar.Calendar(firstweekday=calendar.MONDAY).monthdatescalendar(month.year, month.month)
    first, last = weeks[0][0], weeks[-1][-1]
    activities = services.activities_between(first, last)
    grid = []
    for week in weeks:
        days = []
        for day in week:
            on_day = [a for a in activities if a.start_date <= day <= a.end_date]
            days.append({
                "date": day, "in_month": day.month == month.month, "is_today": day == today,
                "activities": on_day[:CALENDAR_CHIPS_PER_DAY], "more": max(0, len(on_day) - CALENDAR_CHIPS_PER_DAY),
            })
        grid.append(days)
    previous = (month - timedelta(days=1)).replace(day=1)
    following = (month + timedelta(days=32)).replace(day=1)
    return {
        "month": month, "weeks": grid, "previous": previous, "next": following,
        "count": len(activities), "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }


# --- One activity -----------------------------------------------------------------------------------


def tabs(current: str) -> list[dict]:
    return [{"key": key, "label": label, "current": key == current} for key, label in TABS]


def tab_key(raw: str | None) -> str:
    return raw if raw in {key for key, _ in TABS} else DEFAULT_TAB


def registration_summary(activity: Activity, now: datetime | None = None) -> dict:
    """Registered / max slots, open or why not (registrations.services.registration_closed_reason, the one rule),
    and the amber `notice` for staff when a Scheduled activity's registration is closed.
    `link_available`: the public registration link is shown (and copyable) only while registration is open."""
    registered = services.registered_count(activity)
    reason = registration_services.registration_closed_reason(activity, now)
    notice = None
    if reason and activity.status.name == ActivityStatus.SCHEDULED:
        notice = FORM_MISSING_NOTICE if reason == registration_services.REGISTRATION_FORM_NOT_READY else reason
        if reason == registration_services.REGISTRATION_FORM_NOT_READY and registration_services.registration_form_needs_ready(
                activity, registration_services.get_form(activity, FormType.REGISTRATION)):
            notice = FORM_NOT_READY_NOTICE
    return {
        "registered": registered, "max_slots": activity.max_slots, "percent": _percent(registered, activity.max_slots),
        "slots_remaining": services.slots_remaining(activity), "open": reason is None, "reason": reason,
        "notice": notice, "link_available": reason is None,
    }


def attendance_card(activity: Activity) -> dict:
    """The Overview's "Attendance" card (ASSUMPTIONS 434): the pre-registration answers for the meal estimate (Will
    attend / Will not attend, walk-ins) and how many attended each day. Counts from registrations.attendance only."""
    from registrations import attendance

    days = attendance.checkins_by_day(activity)
    labels = len(days) > 1
    return {
        **attendance.pre_registration_counts(activity),
        "days": [{**day, "label": (f"Day {number} · " if labels else "") + format_date(day["day"])}
                 for number, day in enumerate(days, start=1)],
        "any_attendance": any(day["total"] for day in days),
    }


def detail_rows(detail: dict) -> list[tuple[str, str]]:
    """The Overview's fields as readable text (services.get_activity_detail values, formatted like the audit log)."""
    return [
        ("Activity type", detail["activity_type"]),
        ("Participant categories", ", ".join(detail["participant_categories"])),
        ("Start date", format_date(detail["start_date"])),
        ("End date", format_date(detail["end_date"])),
        ("Time", detail["time_text"]),
        ("Venue", detail["venue"]),
        ("Max slots", f"{detail['max_slots']:,}"),
        ("Registration deadline", format_datetime(detail["registration_deadline"])),
        ("Evaluation deadline", format_datetime(detail["evaluation_deadline"]) if detail["evaluation_deadline"] else NO_EVALUATION_DEADLINE),
        ("Budget", format_money(detail["budget_amount"])),  # the budget lines' grand total (ASSUMPTIONS 433)
        ("Fund source", detail["budget"]["fund_source"]),
        ("Created by", f"{detail['created_by']}, {format_datetime(detail['created_at'])}"),
        ("Last updated", format_datetime(detail["updated_at"])),
    ]


def history(activity: Activity, page) -> tuple[services.PageResult, list[dict]]:
    """The History tab: services.get_audit_log (newest first, 20 a page) with readable sentences."""
    result = services.get_audit_log(activity, page=page)
    return result, [{"entry": entry, "sentence": audit_sentence(entry)} for entry in result.items]


def signatory_form_options(member, activity: Activity | None = None) -> dict:
    """The activity form's "Who signs the certificates" (ASSUMPTIONS 365): who is shown first per slot (the activity's
    saved person; on a new activity the active default, or nobody) and whether "Add a person…" is offered
    (can_manage_signatories, ASSUMPTIONS 418), the saved "Title on this certificate" per slot, and whether the member
    may choose at all (can_approve_certificates, ASSUMPTIONS 431; otherwise the slots are read only)."""
    from certificates import services as certificate_services

    titles = {}
    if activity is not None:
        rows = certificate_services.activity_slots(activity)
        current = {slot: row.signatory if row else None for slot, row in rows.items()}
        titles = {slot: row.position_on_certificate or "" for slot, row in rows.items() if row}
    else:
        current = {slot: person if person is not None and person.is_active else None
                   for slot, person in certificate_services.default_signatories().items()}
    return {"current_signatories": current, "current_titles": titles,
            "can_add_person": has_permission(member, Permission.MANAGE_SIGNATORIES),
            "can_choose_signatories": certificate_services.can_choose_signatories(member, activity)}


TEXT_MODE_FIELD = "certificate_text_mode"  # "default" | "custom" inside "Prepare the certificates now"


def posted_certificate_texts(data) -> dict | None:
    """The New activity form's wording when "Custom text for this activity" is chosen inside "Prepare the certificates
    now" ({type: {field: text}}), else None (the default text). Parsing only (ASSUMPTIONS 420)."""
    if data.get(TEXT_MODE_FIELD) != "custom":
        return None
    from certificates import pages as certificate_pages

    return certificate_pages.posted_texts(data)


def split_text_errors(error) -> dict[str, list[str]]:
    """Take the wording errors ("participation-main_sentence", ...) out of the service's ValidationError, so they go
    under the certificate text fields and the rest under the activity's fields."""
    from certificates.services import TEXT_TYPES

    if not hasattr(error, "error_dict"):
        return {}
    keys = [key for key in error.error_dict if key.split("-", 1)[0] in TEXT_TYPES]
    return {key: [message for item in error.error_dict.pop(key) for message in item.messages] for key in keys}


def prepare_section(form, data, text_errors: dict | None = None) -> dict:
    """The New activity form's optional "Prepare the certificates now" (ASSUMPTIONS 420): closed by default; ticked
    after an error stays ticked. Inside: "Who signs the certificates" (signatory_form_section), "Use the default text" /
    "Custom text for this activity" (certificates.pages.new_activity_text_panel) and the sample preview
    (certificates:draft_sample_preview, built from the typed fields). Display only: the services decide."""
    from certificates import pages as certificate_pages

    return {
        "field": form["prepare_certificates"],
        "checked": form.signatories_used(),
        "text": certificate_pages.new_activity_text_panel(data, text_errors),
        "preview_url": reverse("certificates:draft_sample_preview"),
        "types": [{"type": kind, "label": label} for kind, label in certificate_pages.SAMPLE_TYPES],
    }


def signatory_form_section(form, member) -> dict:
    """What the form template shows for "Who signs the certificates": per slot the bound select and title fields,
    each option's usual position (the title's placeholder follows the chosen person) and the amber note for an
    inactive person; the "Add a person…" dialog for can_manage_signatories (ASSUMPTIONS 418). Without
    can_approve_certificates (ASSUMPTIONS 431) `editable` is False: the template shows each slot's person and printed
    title read only with "Only the Director chooses who signs." (the service refuses a change anyway)."""
    from certificates import services as certificate_services

    slots = []
    for slot in ("left", "right"):
        field = form[f"signatory_{slot}"]
        people = form.fields[f"signatory_{slot}"].allowed_people
        selected = str(field.value() or "")
        current = form.signatory_current.get(slot)
        options = []
        for value, label in form.fields[f"signatory_{slot}"].choices:
            person = people.get(int(value)) if str(value).isdigit() else None
            options.append({"value": str(value), "label": label, "position": person.position if person else "",
                            "selected": str(value) == selected})
        chosen = people.get(int(selected)) if selected.isdigit() else None
        slots.append({
            "slot": slot,
            "label": "Left" if slot == "left" else "Right",
            "field": field,
            "title": form[f"title_{slot}"],
            "options": options,
            "placeholder": chosen.position if chosen else "The person's usual position",
            "inactive": current is not None and not current.is_active,
            "locked": current is not None,  # a chosen slot can be switched but never emptied
            "person": current,  # read-only display (ASSUMPTIONS 431)
            "printed_title": (form.fields[f"title_{slot}"].initial or (current.position if current else "")),
        })
    editable = form.signatory_editable
    return {
        "slots": slots,
        "editable": editable,
        "only_director": certificate_services.ONLY_DIRECTOR_CHOOSES,
        "can_add": editable and has_permission(member, Permission.MANAGE_SIGNATORIES),  # "Add a person…" (418)
        "add_url": reverse("certificates:signatory_add_inline"),
        "max_mb": settings.CERTIFICATE_IMAGE_MAX_MB,
        "no_defaults": all(slot["field"].value() in (None, "") for slot in slots),
        "list_url": reverse("certificates:signatories"),
    }


def keep_stored_minutes(cleaned: dict, activity: Activity) -> dict:
    """A datetime-local input has no seconds: when a deadline comes back equal to the stored one cut to the
    minute, keep the stored value, so an untouched deadline is not seen as changed (and re-checked)."""
    for name in ("registration_deadline", "evaluation_deadline"):
        stored, sent = getattr(activity, name), cleaned.get(name)
        if stored and sent and stored.replace(second=0, microsecond=0) == sent:
            cleaned[name] = stored
    return cleaned


def category_donut(by_category: dict) -> dict:
    """The Overview's "Participants by category" donut (ASSUMPTIONS 403) from reports.services.
    activity_participants_by_category (counts unchanged). Attended once anybody is marked as attended, else registered
    (with a note). Whole percentages with the largest-remainder rule (metrics.shares, units=100: they add up to 100 %;
    "–" when the total is 0). A category with 0 stays in the legend without a slice. Laid out by components/donut.html."""
    attended_basis = by_category["attended"] > 0
    key = "attended" if attended_basis else "registered"
    label = "attended" if attended_basis else "registered"
    rows = sorted(by_category["rows"], key=lambda row: (-row[key], row["category"].lower()))
    counts = [row[key] for row in rows]
    total = sum(counts)
    legend = []
    for index, (row, share) in enumerate(zip(rows, shares(counts, units=100))):
        percent_text = "–" if share is None else f"{round(share * 100)}%"
        legend.append({
            "name": row["category"], "count": row[key], "percent_text": percent_text, "color": category_color(index),
            "note": f"{row['registered']:,} registered" if attended_basis else "",
        })
    described = ", ".join(f"{row['name']} {row['count']:,} ({row['percent_text']})" for row in legend)
    return {
        "total": total,
        "label": label,
        "attended_basis": attended_basis,
        "registered": by_category["registered"],
        "segments": [segment for segment in donut_segments(counts) if segment],
        "stroke": DONUT_STROKE,
        "legend": legend,
        "caption": f"Participants by category, {label} (the chart shows the same numbers)",
        "count_header": label.capitalize(),
        "aria_label": f"Participants by category, {total:,} {label}: {described}",
    }


# --- The budget (ASSUMPTIONS 433): the form's table, its preview and the Overview card (display / parsing only) ------

BUDGET_ROW = re.compile(r"^budget-(\d+)-(category|particulars|quantity|unit|days|unit_price)$")
BUDGET_UNITS = ("pax", "person", "pack", "piece", "set", "hour", "day", "lot", "box", "ream", "trip")


def posted_budget_rows(data) -> list[tuple[int, dict]]:
    """The form's budget rows as (row key, {field: text}) in key order; rows left empty are dropped (the service's
    `is_blank`). The service gets only the dicts, so its "budget-<index>-<field>" errors refer to this list's order."""
    from . import budget

    rows: dict[int, dict] = {}
    for name in data:
        match = BUDGET_ROW.match(name)
        if match:
            rows.setdefault(int(match.group(1)), {})[match.group(2)] = data.get(name, "")
    return [(key, row) for key, row in sorted(rows.items()) if not budget.is_blank(row)]


def split_budget_errors(error) -> dict[str, list[str]]:
    """Take the budget errors ("budget-<index>-<field>", "budget_lines") out of the service's ValidationError, so they
    go under the table's cells and the rest under the activity's fields."""
    from . import budget

    if not hasattr(error, "error_dict"):
        return {}
    keys = [key for key in error.error_dict if key.startswith("budget-") or key == budget.LINES_KEY]
    return {key: [message for item in error.error_dict.pop(key) for message in item.messages] for key in keys}


def _saved_rows(activity: Activity) -> list[dict]:
    from . import budget

    return [{"category": str(line.category_id), "particulars": line.particulars,
             "quantity": budget.quantity_text(line.quantity), "unit": line.unit, "days": str(line.days),
             "unit_price": f"{line.unit_price:.2f}"} for line in budget.saved_lines(activity)]


def budget_section(form, activity: Activity | None, posted: list[dict] | None = None,
                   errors: dict | None = None) -> dict:
    """The activity form's "Budget" card: the fund source / account fields, the rows (what was posted after an error,
    else the saved lines, else one empty row) with their errors and totals (budget.preview: the service's own rules),
    the categories (active + the ones already used) and the preview URL. Display only."""
    from . import budget
    from .models import BudgetCategory

    rows = posted if posted is not None else (_saved_rows(activity) if activity is not None else [])
    used = budget.used_category_ids(activity)
    calculated = budget.preview(rows, used)
    errors = errors or {}
    shown = []
    for index, row in enumerate(rows or [{}]):
        shown.append({
            "key": index, "values": {field: row.get(field, "") for field in budget.FIELDS},
            "errors": {field: " ".join(errors.get(budget.row_key(index, field), [])) for field in budget.FIELDS},
            "total": calculated["row_totals"].get(index) if rows else None,
        })
    for row in shown:
        if not row["values"]["days"]:
            row["values"]["days"] = "1"
    categories = BudgetCategory.objects.filter(Q(is_active=True) | Q(pk__in=used)).order_by("sort_order", "name")
    return {
        "fund_source": form["fund_source"], "fund_account": form["fund_account"],
        "rows": shown, "next_key": len(shown), "max_rows": budget.MAX_LINES,
        "categories": categories, "units": BUDGET_UNITS,
        "table_errors": errors.get(budget.LINES_KEY, []),
        "category_totals": calculated["category_totals"], "total": calculated["total"],
        "preview_url": reverse("activities:budget_preview"),
        "activity_id": activity.pk if activity is not None else "",
    }


def budget_preview(data) -> dict:
    """activities:budget_preview: the totals of what is typed (budget.preview), keyed by the form's own row keys."""
    from . import budget

    keyed = posted_budget_rows(data)
    activity_id = str(data.get("activity", ""))
    used = budget.used_category_ids(Activity(pk=int(activity_id))) if activity_id.isdigit() else set()
    calculated = budget.preview([row for _, row in keyed], used)
    row_totals = {key: calculated["row_totals"].get(index) for index, (key, _) in enumerate(keyed)}
    return {"row_totals": row_totals, "category_totals": calculated["category_totals"], "total": calculated["total"],
            "problems": sum(1 for _ in calculated["errors"])}


def budget_card(activity: Activity) -> dict:
    """The Overview's "Budget" card (budget.budget_summary): fund source (or "Not set"), account, the Particulars /
    Quantity / Unit Cost / Total Amount table, the totals per category and the grand total."""
    from . import budget

    return budget.budget_summary(activity)
