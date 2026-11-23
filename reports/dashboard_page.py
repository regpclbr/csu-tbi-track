"""The staff dashboard page (/): turns the numbers into what the template shows.

Every number comes from reports.metrics (through services.dashboard, exactly like the dashboard API)
or from an existing service (review queue, open activities, registered count, audit log). This module
only picks, groups and lays out those results (percentages for bar widths, donut segments, sentences).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.db.models import Count, Min
from django.urls import reverse
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, has_permission
from activities.formatting import audit_sentence, changed_by_name
from activities.models import Activity, ActivityType
from certificates.models import Certificate
from activities.services import attended_count, open_for_registration, recent_audit_entries, registered_count
from config.frontend import initials
from documents.services import review_queue, today_in_manila

from . import metrics, services
from .content import range_in_words

UPCOMING_SHOWN = 4  # the first is the "Next activity" spotlight, the rest the list under it
UPCOMING_LIST_SHOWN = UPCOMING_SHOWN - 1  # rows in the "Upcoming activities" list under the spotlight
ATTENTION_SHOWN = 5  # "Needs attention" items shown before "Show N more"
ATTENTION_TITLES_SHOWN = 2  # activity titles written under a grouped "Needs attention" item

# Donut and legend colours, in order. Categories are master data (never a fixed number): the colours repeat.
# Calm colours of the brand family (ASSUMPTIONS 413): the primary navy, a muted cyan, green, soft amber, then violet, grey,
# clay and a light blue. Every pair stays apart also with simulated protanopia, deuteranopia and tritanopia (smallest
# difference dE 15); the legend and the text table carry the numbers.
CATEGORY_COLORS = ["#2A4494", "#5AAFC8", "#2F7A50", "#EDC06A", "#8C6BB1", "#6B7686", "#D98B7A", "#C7DCF2"]
DONUT_STROKE = 7  # the ring's width in the donut's SVG units (circumference 100)
DONUT_GAP = 1.5  # space between two segments, same units

QUICK_RANGES = [
    ("this_month", "This month"),
    ("this_quarter", "This quarter"),
    ("this_year", "This year"),
    ("last_year", "Last year"),
    ("custom", "Custom"),
]


# --- Filters ---------------------------------------------------------------------------------------


def quick_range(key: str, today: date) -> tuple[date, date] | None:
    """Start and end of a quick choice (Manila calendar), or None for "custom" / unknown keys."""
    if key == "this_month":
        start = today.replace(day=1)
        next_month = (start + timedelta(days=32)).replace(day=1)
        return start, next_month - timedelta(days=1)
    if key == "this_quarter":
        first_month = 3 * ((today.month - 1) // 3) + 1
        start = date(today.year, first_month, 1)
        end = date(today.year + 1, 1, 1) if first_month == 10 else date(today.year, first_month + 3, 1)
        return start, end - timedelta(days=1)
    if key == "this_year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if key == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    return None


def matching_quick_range(start: date, end: date, today: date) -> str:
    """The quick choice that gives exactly this range, else "custom"."""
    for key, _label in QUICK_RANGES:
        if quick_range(key, today) == (start, end):
            return key
    return "custom"


def filter_params(params: dict, today: date) -> dict:
    """The parameters for metrics.parse_filters: a quick choice (range=...) wins over typed dates."""
    chosen = quick_range(params.get("range", ""), today)
    start, end = (chosen[0].isoformat(), chosen[1].isoformat()) if chosen else (params.get("start"), params.get("end"))
    return {"start": start, "end": end, "activity_type": params.get("activity_type")}


# --- The page --------------------------------------------------------------------------------------


@dataclass
class AttentionItem:
    tone: str  # "danger" (overdue), "warning" (due soon), "primary" (waiting for someone)
    title: str
    detail: str
    action_label: str = ""
    action_url: str = ""


@dataclass
class DashboardPage:
    filters: metrics.ReportFilters
    range_key: str
    range_text: str
    errors: list[str]
    data: dict  # services.dashboard(): exactly what the dashboard API returns
    cards: dict
    can_create_activity: bool
    review: dict
    open_count: int
    attention: list[AttentionItem]
    upcoming: list[dict]
    categories: dict
    participant_mix: dict  # mix_chart of metrics.period_participant_mix (ASSUMPTIONS 424)
    statuses: dict
    conducted_by_type: list[dict]
    recent_changes: list[dict]
    greeting: str = ""  # "Good morning" / "Good afternoon" / "Good evening" (Manila time)
    first_name: str = ""
    activity_types: list = field(default_factory=list)
    range_options: list = field(default_factory=list)  # (key, label) for the date range select
    spotlight: dict | None = None  # the card beside the banner (services.dashboard_spotlight, ASSUMPTIONS 351)
    can_approve_certificates: bool = False  # only the wording of the certificate card (ASSUMPTIONS 357)

    attention_shown: int = ATTENTION_SHOWN

    @property
    def certificates_pill(self) -> str:
        """S5's certificate count for the KPI card: "100 certificates"."""
        count = self.cards["certificates_waiting_for_approval"]["certificates"]
        return f"{count:,} {'certificate' if count == 1 else 'certificates'}"

    @property
    def certificates_note(self) -> str:
        """Under S5's number (which counts activities, ASSUMPTIONS 369): who it waits for."""
        who = "need your approval" if self.can_approve_certificates else "need approval"
        count = self.cards["certificates_waiting_for_approval"]["value"]
        return f"{'activity' if count == 1 else 'activities'} · {who}"

    @property
    def attention_hidden(self) -> int:
        return max(0, len(self.attention) - self.attention_shown)

    @property
    def next_activity(self) -> dict | None:
        """The spotlight card: an activity happening now, else the next upcoming one (services.dashboard_spotlight)."""
        return self.spotlight

    @property
    def upcoming_after_next(self) -> list[dict]:
        """The "Upcoming activities" list: the upcoming ones the spotlight does not already show."""
        if self.spotlight and not self.spotlight["ongoing"]:
            return self.upcoming[1:UPCOMING_SHOWN]
        return self.upcoming[:UPCOMING_LIST_SHOWN]

    @property
    def attention_count_text(self) -> str:
        count = len(self.attention)
        return f"{count} item" if count == 1 else f"{count} items"

    @property
    def attention_tone(self) -> str:
        """The Needs attention card's accent (display only, ASSUMPTIONS 413): red when something is overdue, amber when
        something is due soon, else blue (only approvals / reviews wait)."""
        tones = {item.tone for item in self.attention}
        return "danger" if "danger" in tones else "warning" if "warning" in tones else "info"

    @property
    def attention_primary_index(self) -> int | None:
        """The one solid button of the Needs attention card (ASSUMPTIONS 413): the first approval or review the member can
        do (action "Approve" / "Review"); every other row's button is secondary. None when there is none."""
        for index, item in enumerate(self.attention):
            if item.tone == "primary" and item.action_label in ("Approve", "Review"):
                return index
        return None


def build_dashboard(member: TBIMember, params: dict, now: datetime | None = None) -> DashboardPage:
    """Everything the dashboard page shows, for any active staff member (services.dashboard checks it)."""
    today = today_in_manila(now)
    errors: list[str] = []
    try:
        data = services.dashboard(member, filter_params(params, today), now)
    except ValidationError as error:
        messages = error.message_dict if hasattr(error, "error_dict") else {"": error.messages}
        errors = [message for values in messages.values() for message in values]
        data = services.dashboard(member, {}, now)  # the default range (this year)
    filters = metrics.ReportFilters(data["filters"]["start"], data["filters"]["end"], data["filters"]["activity_type"])
    cards = {card["key"]: card for card in data["cards"]}
    can_create = has_permission(member, Permission.CREATE_ACTIVITY)
    can_approve_certificates = has_permission(member, Permission.APPROVE_CERTIFICATES)
    review = _review(member, cards, has_permission(member, Permission.APPROVE_DOCUMENTS), now)
    upcoming = _upcoming(cards["upcoming_activities"]["next"][:UPCOMING_SHOWN])

    return DashboardPage(
        filters=filters,
        range_key=matching_quick_range(filters.start, filters.end, today),
        range_text=range_in_words(filters.start, filters.end),
        errors=errors,
        data=data,
        cards=cards,
        can_create_activity=can_create,
        review=review,
        open_count=_open_count(filters, now),
        attention=_attention(filters, cards, review, can_approve_certificates, now),
        can_approve_certificates=can_approve_certificates,
        upcoming=upcoming,
        spotlight=_spotlight(services.dashboard_spotlight(filters, now, cards["upcoming_activities"]["next"]), upcoming),
        categories=_categories(data["charts"]["by_category"]),
        participant_mix=mix_chart(data["charts"]["participant_mix"], activity_count_note=True),
        statuses=_statuses(cards["activities_by_status"]["by_status"]),
        conducted_by_type=_bars(data["charts"]["by_type"]),
        recent_changes=[
            {"sentence": audit_sentence(entry), "changed_at": entry.changed_at, "member": changed_by_name(entry),
             "initials": initials(changed_by_name(entry))}
            for entry in recent_audit_entries()
        ],
        greeting=greeting(now),
        first_name=member.name.split()[0] if member.name.split() else "",
        activity_types=list(ActivityType.objects.filter(is_active=True).order_by("name")),
        range_options=_range_options(today),
    )


def greeting(now: datetime | None = None) -> str:
    """The banner's greeting for the Manila time of day: morning before 12:00, afternoon before 18:00."""
    hour = timezone.localtime(now or timezone.now()).hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


def _range_options(today: date) -> list[tuple[str, str]]:
    """The quick choices with their dates in words, e.g. ("this_year", "This year · January 1 – December 31, 2026")."""
    options = []
    for key, label in QUICK_RANGES:
        chosen = quick_range(key, today)
        options.append((key, f"{label} · {range_in_words(*chosen)}" if chosen else "Custom range…"))
    return options


def _review(member: TBIMember, cards: dict, can_approve: bool, now: datetime | None = None) -> dict:
    """Members who can approve documents: the documents waiting for THEIR review (review_queue, their own uploads
    too; all activity types, like the sidebar badge). Others: S4, waiting for any reviewer (read only)."""
    if can_approve:
        queue = review_queue(member).aggregate(count=Count("pk"), oldest=Min("uploaded_at"))
        oldest = today_in_manila(queue["oldest"]) if queue["oldest"] else None
        days = (today_in_manila(now) - oldest).days if oldest else None
        text = "" if days is None else "oldest today" if days == 0 else f"oldest {days} d"
        return {"mine": True, "count": queue["count"], "oldest": oldest, "oldest_text": text}
    return {"mine": False, "count": cards["documents_waiting_for_review"]["value"], "oldest": None, "oldest_text": ""}


def _open_count(filters: metrics.ReportFilters, now: datetime | None) -> int:
    """Activities open for registration now (activities.services.open_for_registration, the one rule shared with
    the Activities card and /register/), narrowed to the activity type filter like the other "now" cards."""
    activities = open_for_registration(now=now)
    if filters.activity_type_id:
        activities = activities.filter(activity_type_id=filters.activity_type_id)
    return activities.count()


def _group_by_type(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["document_type"], []).append(row)
    return grouped


def _titles(rows: list[dict]) -> str:
    titles = list(dict.fromkeys(row["title"] for row in rows))
    shown = ", ".join(titles[:ATTENTION_TITLES_SHOWN])
    more = len(titles) - ATTENTION_TITLES_SHOWN
    return f"{shown} and {more} more" if more > 0 else shown


def _day(value: date) -> str:
    return f"{value:%b} {value.day}"


def documents_tab_url(row: dict) -> str:
    """The activity's Documents tab with the type preselected (a group of several activities: its first row,
    the oldest due date; the item's text names all of them)."""
    url = reverse("activities:detail", args=[row["activity_id"]])
    return f"{url}?{urlencode({'tab': 'documents', 'type': row['document_type_id']})}"


def certificates_url(by_activity: list[dict]) -> str:
    """One activity: its Certificates tab (the approval screen). Several: the Certificates page, narrowed to the
    certificates waiting for approval."""
    if len(by_activity) == 1:
        return reverse("activities:detail", args=[by_activity[0]["activity_id"]]) + "?tab=certificates"
    return f"{reverse('certificates:list')}?status={Certificate.Status.GENERATED}"


def certificates_waiting_text(card: dict) -> str:
    """"2 activities need certificate approval" from S5 (ASSUMPTIONS 369: counted as activities)."""
    return f"{card['value']} {'activity needs' if card['value'] == 1 else 'activities need'} certificate approval"


def _attention(filters: metrics.ReportFilters, cards: dict, review: dict, can_approve_certificates: bool,
               now: datetime | None) -> list[AttentionItem]:
    """Overdue required documents (red), documents due soon (amber), my review queue, certificates waiting for the
    approval (counted as activities)."""
    items: list[AttentionItem] = []
    rows = metrics.document_attention(filters, now)
    for type_name, group in _group_by_type(rows["overdue"]).items():
        first = group[0]  # oldest due date first
        if len(group) == 1:
            verb = "is missing the" if first["state"] == metrics.MISSING else "is waiting for approval of the"
            title = f"{first['title']} {verb} {type_name}"
            detail = f"Overdue since {_day(first['due_date'] + timedelta(days=1))}"
        else:
            title = f"{type_name} is overdue for {len(group)} activities"
            detail = _titles(group)
        action = "Upload" if any(row["state"] == metrics.MISSING for row in group) else "Open"
        items.append(AttentionItem("danger", title, detail, action, documents_tab_url(first)))
    for type_name, group in _group_by_type(rows["due_soon"]).items():
        first = group[0]
        if len(group) == 1:
            title = f"{first['title']} needs the {type_name}"
            detail = f"Due {_day(first['due_date'])} · not uploaded yet"
        else:
            title = f"{type_name} is due soon for {len(group)} activities"
            detail = f"{_titles(group)} · first due {_day(first['due_date'])}"
        items.append(AttentionItem("warning", title, detail, "Upload", documents_tab_url(first)))

    if review["mine"] and review["count"]:
        noun = "document is" if review["count"] == 1 else "documents are"
        items.append(AttentionItem(
            "primary", f"{review['count']} {noun} waiting for your review",
            f"Oldest uploaded {_day(review['oldest'])}" if review["oldest"] else "", "Review", reverse("documents:review"),
        ))

    waiting = cards["certificates_waiting_for_approval"]
    if waiting["value"]:
        by_activity = metrics.certificates_waiting_by_activity(filters)
        detail = ", ".join(row["title"] for row in by_activity[:ATTENTION_TITLES_SHOWN])
        if waiting["value"] > ATTENTION_TITLES_SHOWN:
            detail += " and more"
        # "2 activities need certificate approval" (ASSUMPTIONS 369: one approval per activity)
        items.append(AttentionItem(
            "primary", certificates_waiting_text(waiting), f"Certificates · {detail}",
            "Approve" if can_approve_certificates else "Open", certificates_url(by_activity),
        ))
    return items


def _upcoming(next_rows: list[dict]) -> list[dict]:
    """The next Scheduled activities (S2's list) with type, categories and the registered count
    (activities.services.registered_count, the Step 4 rule)."""
    by_id = {
        activity.pk: activity
        for activity in Activity.objects.filter(pk__in=[row["activity_id"] for row in next_rows])
        .select_related("status", "activity_type")
        .prefetch_related("participant_categories")
    }
    upcoming = []
    for row in next_rows:
        activity = by_id[row["activity_id"]]
        registered = registered_count(activity)
        upcoming.append({
            "activity": activity,
            "categories": sorted(c.name for c in activity.participant_categories.all()),
            "registered": registered,
            "percent": min(100, round(100 * registered / activity.max_slots)) if activity.max_slots else 0,
        })
    return upcoming


def _spotlight(chosen: services.Spotlight | None, upcoming: list[dict]) -> dict | None:
    """What the spotlight card shows. Upcoming: the first upcoming row (registered / max slots). Happening now:
    attended / registered (activities.services counts), "Mark attendance", and the other ongoing activities."""
    if chosen is None:
        return None
    if chosen.kind == services.NEXT_UP:
        return {**upcoming[0], "ongoing": False, "more_ongoing": 0}
    activity = chosen.activity
    registered, attended = registered_count(activity), attended_count(activity)
    return {
        "activity": activity,
        "categories": sorted(c.name for c in activity.participant_categories.all()),
        "registered": registered,
        "attended": attended,
        "percent": min(100, round(100 * attended / registered)) if registered else 0,
        "ongoing": True,
        "more_ongoing": chosen.more_ongoing,
        "more_url": f"{reverse('activities:list')}?{urlencode({'status': activity.status_id})}",
    }


def _percent(part: int, whole: int) -> float:
    return 100 * part / whole if whole else 0


def donut_segments(counts: list[int]) -> list[dict | None]:
    """Donut segments for these counts, in order (SVG circle with a circumference of 100, so a segment's dash length is
    its percentage of the total). Colour i is CATEGORY_COLORS[i] (repeating). A count of 0 gets None: a legend row but
    no slice. Shared by the dashboard and the activity page's "Participants by category" (ASSUMPTIONS 403)."""
    total = sum(counts)
    shown = sum(1 for count in counts if count)
    # Rounded ends (stroke-linecap: round, width DONUT_STROKE) stick out half the stroke width at each end:
    # with two or more segments each one is drawn shorter, so a small gap stays between them.
    inset = DONUT_STROKE / 2 + DONUT_GAP / 2 if shown > 1 else 0
    segments, offset = [], 0.0
    for index, count in enumerate(counts):
        if not count:
            segments.append(None)
            continue
        share = _percent(count, total)
        # dash + gap = 100 (the circumference), so the pattern wraps round the circle; offset 25 starts at 12 o'clock
        length = max(share - 2 * inset, 0.001)
        segments.append({"color": category_color(index), "length": round(length, 3), "gap": round(100 - length, 3),
                         "offset": round(25 - offset - inset, 3)})
        offset += share
    return segments


def category_color(index: int) -> str:
    return CATEGORY_COLORS[index % len(CATEGORY_COLORS)]


def _categories(rows: list[dict]) -> dict:
    """N9 attended per registration category: legend rows and donut segments (`donut_segments`), laid out by
    components/donut.html."""
    ordered = sorted(rows, key=lambda r: (-r["attended"], r["name"]))
    total = sum(row["attended"] for row in ordered)
    segments = donut_segments([row["attended"] for row in ordered])
    legend = []
    for index, row in enumerate(ordered):
        percent = round(_percent(row["attended"], total))
        legend.append({"name": row["name"], "attended": row["attended"], "count": row["attended"], "percent": percent,
                       "percent_text": f"{percent}%", "color": category_color(index)})
    described = ", ".join(f"{row['name']} {row['count']:,} ({row['percent_text']})" for row in legend)
    return {"total": total, "legend": legend, "segments": [s for s in segments if s], "stroke": DONUT_STROKE,
            "label": "attended", "caption": "Attendees by participant category (the chart shows the same numbers)",
            "count_header": "Attendees", "aria_label": f"Attendees by category, {total:,} attended: {described}"}


def mix_chart(mix: dict, activity_count_note: bool = False) -> dict:
    """Lay out a participant mix (reports.metrics.participant_mix / period_participant_mix, ASSUMPTIONS 424) for
    components/participant_mix.html: two 100 % bars (Registered, Attended), a text table with counts and whole
    percentages, NAVIGATU's guideline and the result of metrics' check. Display only: never counts or checks here.
    `activity_count_note`: the dashboard's line "N of M checked activities are outside the guideline"."""
    rows = []
    for index, row in enumerate(mix["rows"]):
        rows.append({
            "group": row["group"], "color": category_color(index),
            "registered": row["registered"], "registered_text": _share_text(row["registered_share"]),
            "attended": row["attended"], "attended_text": _share_text(row["attended_share"]),
            "registered_width": round(_percent(row["registered"], mix["registered"]), 2),
            "attended_width": round(_percent(row["attended"], mix["attended"]), 2),
        })
    bars = []
    for key, label in (("registered", "Registered"), ("attended", "Attended")):
        described = ", ".join(f"{row['group']} {row[key]:,} ({row[key + '_text']})" for row in rows)
        bars.append({
            "label": label, "total": mix[key],
            "segments": [{"color": row["color"], "width": row[key + "_width"]} for row in rows if row[key]],
            "aria_label": f"{label}: {mix[key]:,} in total" + (f": {described}" if mix[key] else ""),
        })
    check = mix.get("check")
    if check is None:
        status = None
    elif check["within"]:
        status = {"within": True, "text": f"Within the guideline ({_basis_word(mix['basis'])})."}
    else:
        status = {"within": False,
                  "text": f"Outside the guideline ({_basis_word(mix['basis'])}): {'; '.join(check['problems'])}. "
                          "Registration stays open."}
    note = ""
    if activity_count_note:
        checked, outside = mix.get("activities_checked", 0), mix.get("activities_outside", 0)
        note = (f"{outside} of {checked} checked {'activity is' if checked == 1 else 'activities are'} outside the "
                "guideline (only activities open to Faculty and to Incubatees or MSMEs are checked)." if checked else
                "No activity in this period is open to both Faculty and Incubatees or MSMEs, so none is checked.")
    elif not mix.get("checked"):
        note = "Not checked: this activity is not open to both Faculty and Incubatees or MSMEs."
    return {"rows": rows, "bars": bars, "guideline": metrics.guideline_text(), "status": status, "note": note,
            "empty": not mix["registered"]}


def _share_text(share: float | None) -> str:
    return "–" if share is None else f"{round(share * 100)}%"


def _basis_word(basis: str | None) -> str:
    return "attendees" if basis == "attended" else "registrations, nobody is marked as attended yet"


STATUS_COLORS = {"Draft": "bg-status-draft", "Scheduled": "bg-status-scheduled", "Ongoing": "bg-status-ongoing", "Conducted": "bg-status-conducted"}


def _statuses(rows: list[dict]) -> dict:
    """S1 as one stacked bar."""
    total = sum(row["count"] for row in rows)
    return {
        "total": total,
        "rows": [{**row, "percent": round(_percent(row["count"], total), 2), "color": STATUS_COLORS.get(row["status"], "bg-slate-400")} for row in rows],
    }


def _bars(rows: list[dict]) -> list[dict]:
    """N2 Conducted activities per type, most first; types with none are left out."""
    rows = sorted((row for row in rows if row["activities"]), key=lambda r: (-r["activities"], r["name"]))
    largest = max((row["activities"] for row in rows), default=0)
    return [{"name": row["name"], "count": row["activities"], "percent": round(_percent(row["activities"], largest), 2)} for row in rows]
