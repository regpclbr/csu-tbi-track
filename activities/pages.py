"""What the Activities pages show (activities/page_views.py). Display helpers only: reading the query
string, the month grid, the tabs and the readable detail rows. Every rule and number comes from
activities.services and registrations.services (never repeated here)."""

import calendar
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.utils import timezone

from registrations import services as registration_services

from . import services
from .formatting import audit_sentence, format_date, format_datetime, format_money
from .models import Activity, ActivityStatus

LIST, CALENDAR = "list", "calendar"

# The sort options of services.list_activities (SORT_OPTIONS), with readable labels (a test checks they match).
SORT_CHOICES = [
    ("-start_date", "Start date: newest first"),
    ("start_date", "Start date: oldest first"),
    ("title", "Title: A to Z"),
    ("-title", "Title: Z to A"),
    ("-registered", "Most registered"),
    ("registered", "Fewest registered"),
    ("-created_at", "Recently created"),
    ("created_at", "First created"),
]

# Tabs of the activity page: (key, label, phase that builds it; None = built).
TABS = [
    ("overview", "Overview", None),
    ("history", "History", None),
    ("forms", "Forms", None),
    ("registrations", "Registrations", None),
    ("evaluation", "Evaluation", None),
    ("documents", "Documents", "F4"),
    ("certificates", "Certificates", "F4"),
]
DEFAULT_TAB = "overview"

# Shown to staff instead of the public wording when only the registration form is missing (owner request, F2a).
FORM_MISSING_NOTICE = "Registration form not created yet. Create it in the Forms tab (phase F2b)."


@dataclass
class ListFilters:
    """The Activities list's query string, checked. Bad values are dropped and explained in `errors`."""

    q: str = ""
    activity_type: int | None = None
    status: int | None = None
    participant_category: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    sort: str = services.DEFAULT_SORT
    page: str = "1"
    view: str = LIST
    month: date = field(default_factory=lambda: timezone.localdate().replace(day=1))
    errors: list[str] = field(default_factory=list)

    def for_service(self) -> dict:
        """The filters in the form services.list_activities() takes."""
        return {
            "search": self.q, "activity_type": self.activity_type, "status": self.status,
            "participant_category": self.participant_category, "date_from": self.date_from,
            "date_to": self.date_to, "sort": self.sort,
        }

    def query(self, **changes) -> str:
        """The URL query for these filters (without empty values), e.g. to keep them in a link."""
        values = replace(self, **changes) if changes else self
        params = {
            "q": values.q, "activity_type": values.activity_type or "", "status": values.status or "",
            "participant_category": values.participant_category or "",
            "date_from": values.date_from.isoformat() if values.date_from else "",
            "date_to": values.date_to.isoformat() if values.date_to else "",
            "sort": "" if values.sort == services.DEFAULT_SORT else values.sort,
            "view": "" if values.view == LIST else values.view,
            "month": values.month.strftime("%Y-%m") if values.view == CALENDAR else "",
        }
        return urlencode({key: value for key, value in params.items() if value not in ("", None)})

    @property
    def any_set(self) -> bool:
        return bool(self.q or self.activity_type or self.status or self.participant_category or self.date_from or self.date_to)


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


def parse_list_filters(params, today: date | None = None) -> ListFilters:
    """Read ?q, activity_type, status, participant_category, date_from, date_to, sort, page, view, month."""
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
    sort = params.get("sort") or services.DEFAULT_SORT
    if sort in services.SORT_OPTIONS:
        filters.sort = sort
    else:
        errors.append("Ignored an unknown sort order.")
    if filters.date_from and filters.date_to and filters.date_to < filters.date_from:
        errors.append("Ignored the dates: “To” is before “From”.")
        filters.date_from = filters.date_to = None
    if month := (params.get("month") or "").strip():
        try:
            filters.month = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            errors.append("Ignored the month: use YYYY-MM.")
    return filters


# --- List rows and calendar ---------------------------------------------------------------------


def _percent(registered: int, max_slots: int) -> int:
    return min(100, round(100 * registered / max_slots)) if max_slots else 0


def list_rows(activities: list[Activity]) -> list[dict]:
    """Rows of the list: the activity, its categories and registered/max (`registered` comes from
    services.list_activities)."""
    return [
        {
            "activity": activity,
            "categories": sorted(category.name for category in activity.participant_categories.all()),
            "registered": activity.registered,
            "percent": _percent(activity.registered, activity.max_slots),
        }
        for activity in activities
    ]


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
    return [{"key": key, "label": label, "phase": phase, "current": key == current} for key, label, phase in TABS]


def tab_key(raw: str | None) -> str:
    return raw if raw in {key for key, _, _ in TABS} else DEFAULT_TAB


def tab_phase(key: str) -> str | None:
    return next(phase for tab, _, phase in TABS if tab == key)


def registration_summary(activity: Activity, now: datetime | None = None) -> dict:
    """Registered / max slots, open or why not (registrations.services.registration_closed_reason, the one rule),
    and the amber `notice` for staff when a Scheduled activity's registration is closed.
    `link_available`: the public registration link is shown (and copyable) only while registration is open."""
    registered = services.registered_count(activity)
    reason = registration_services.registration_closed_reason(activity, now)
    notice = None
    if reason and activity.status.name == ActivityStatus.SCHEDULED:
        notice = FORM_MISSING_NOTICE if reason == registration_services.REGISTRATION_FORM_NOT_READY else reason
    return {
        "registered": registered, "max_slots": activity.max_slots, "percent": _percent(registered, activity.max_slots),
        "slots_remaining": services.slots_remaining(activity), "open": reason is None, "reason": reason,
        "notice": notice, "link_available": reason is None,
    }


def detail_rows(detail: dict) -> list[tuple[str, str]]:
    """The Overview's fields as readable text (services.get_activity_detail values, formatted like the audit log)."""
    return [
        ("Activity type", detail["activity_type"]),
        ("Participant categories", ", ".join(detail["participant_categories"])),
        ("Start date", format_date(detail["start_date"])),
        ("End date", format_date(detail["end_date"])),
        ("Time schedule", detail["time_schedule"]),
        ("Venue", detail["venue"]),
        ("Max slots", f"{detail['max_slots']:,}"),
        ("Registration deadline", format_datetime(detail["registration_deadline"])),
        ("Evaluation deadline", format_datetime(detail["evaluation_deadline"])),
        ("Budget", format_money(detail["budget_amount"])),
        ("Created by", f"{detail['created_by']}, {format_datetime(detail['created_at'])}"),
        ("Last updated", format_datetime(detail["updated_at"])),
    ]


def history(activity: Activity, page) -> tuple[services.PageResult, list[dict]]:
    """The History tab: services.get_audit_log (newest first, 20 a page) with readable sentences."""
    result = services.get_audit_log(activity, page=page)
    return result, [{"entry": entry, "sentence": audit_sentence(entry)} for entry in result.items]


def keep_stored_minutes(cleaned: dict, activity: Activity) -> dict:
    """A datetime-local input has no seconds: when a deadline comes back equal to the stored one cut to the
    minute, keep the stored value, so an untouched deadline is not seen as changed (and re-checked)."""
    for name in ("registration_deadline", "evaluation_deadline"):
        stored, sent = getattr(activity, name), cleaned.get(name)
        if stored and sent and stored.replace(second=0, microsecond=0) == sent:
            cleaned[name] = stored
    return cleaned
