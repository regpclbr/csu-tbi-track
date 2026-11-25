"""What the report generator pages show (ASSUMPTIONS 442-443). Display only: every rule is in reports.generator and
reports.paper_sheets."""

from django.conf import settings
from django.urls import reverse

from activities.models import Activity, ParticipantCategory
from assistant import gemini
from registrations.attendance import activity_days

from . import generator, paper_sheets
from .draft_facts import long_date
from .models import PaperSheetRead, ReportAttachment, ReportChatMessage, ReportDraft, ReportDraftVersion

POLL_SECONDS = 3
STATE_TONES = {ReportDraftVersion.State.PENDING: "neutral", ReportDraftVersion.State.RUNNING: "info",
               ReportDraftVersion.State.READY: "success", ReportDraftVersion.State.FAILED: "danger"}
SHEET_TONES = {PaperSheetRead.State.READING: "info", PaperSheetRead.State.READ: "warning",
               PaperSheetRead.State.FAILED: "danger", PaperSheetRead.State.CONFIRMED: "success"}
EXAMPLE_REQUESTS = [
    "Write the Activity Report from the attached program and my notes.",
    "Make the highlights shorter and mention the partner agencies.",
    "Add a recommendation about starting registration earlier.",
]


def activity_choices() -> list[Activity]:
    return list(Activity.objects.exclude(status__name="Draft").order_by("-start_date", "title")
                .only("pk", "title", "start_date"))


def new_form(data=None, errors: dict | None = None) -> dict:
    data = data or {}
    return {
        "kinds": ReportDraft.Kind.choices, "kind": data.get("kind", ReportDraft.Kind.ACTIVITY_REPORT),
        "scope": data.get("scope", "activity"), "activities": activity_choices(),
        "activity_id": str(data.get("activity", "")), "start": data.get("start", ""), "end": data.get("end", ""),
        "title": data.get("title", ""), "office_line": data.get("office_line", ""),
        "implementing_unit": data.get("implementing_unit", ""), "project_leader": data.get("project_leader", ""),
        "message": data.get("message", ""), "errors": errors or {},
        "message_max": settings.REPORT_GENERATOR_MESSAGE_MAX, "max_files": settings.REPORT_GENERATOR_MAX_ATTACHMENTS,
        "default_unit": settings.REPORT_IMPLEMENTING_UNIT, "ai_off": not gemini.is_configured(),
    }


def scope_text(draft: ReportDraft) -> str:
    if draft.activity_id:
        return draft.activity.title
    return f"{long_date(draft.date_range_start)} – {long_date(draft.date_range_end)}"


def draft_rows(member) -> list[dict]:
    rows = []
    for draft in generator.my_drafts(member)[:100]:
        latest = generator.latest_version(draft)
        rows.append({"draft": draft, "kind": draft.get_kind_display(), "scope": scope_text(draft), "latest": latest, "tone": STATE_TONES.get(latest.state, "neutral") if latest else "neutral",
                     "url": reverse("reports:generator_draft", args=[draft.pk])})
    return rows


def _section_views(version: ReportDraftVersion) -> list[dict]:
    views = []
    for key, label, how in generator.SECTIONS[version.draft.kind]:
        value = version.sections.get(key) or {}
        views.append({"key": key, "label": label, "how": how, "text": value.get("text", ""),
                      "ai": bool(value.get("ai")), "unverified": value.get("unverified", [])})
    return views


def version_view(member, version: ReportDraftVersion, is_latest: bool) -> dict:
    """One version: its state, progress, sections (editable when it is the newest and ready), the facts preview and the
    actions with their refusal reasons."""
    draft = version.draft
    refused = generator.export_refused_reason(version)
    facts = version.facts or {}
    return {
        "version": version, "is_latest": is_latest, "tone": STATE_TONES[version.state],
        "working": version.state in (ReportDraftVersion.State.PENDING, ReportDraftVersion.State.RUNNING),
        "sections": _section_views(version) if version.state == ReportDraftVersion.State.READY else [],
        "editable": is_latest and version.state == ReportDraftVersion.State.READY,
        "facts": facts, "photo_count": len(facts.get("photos", [])),
        "count_rows": [(key.replace("_", " ").capitalize(), value) for key, value in (facts.get("counts") or {}).items()],
        "attendance_days": len((facts.get("annex") or {}).get("attendance_pages", [])),
        "confirmation_count": len((facts.get("annex") or {}).get("confirmation_rows", [])),
        "export_refused": refused,
        "save_refused": refused or (generator.NO_ACTIVITY_TO_SAVE if draft.activity_id is None else None)
        or (generator.ALREADY_SAVED if version.saved_document_id else None),
        "submit_refused": refused or (generator.ALREADY_SUBMITTED if version.submission_id else None),
        "ai_label": generator.AI_LABEL,
        "status_url": reverse("reports:generator_version_status", args=[version.pk]),
        "poll_seconds": POLL_SECONDS,
    }


def attachment_rows(draft: ReportDraft) -> list[dict]:
    sheets = {}
    for sheet in paper_sheets.draft_sheets(draft):
        sheets.setdefault(sheet.attachment_id, sheet)
    return [{"attachment": a, "sheet": sheets.get(a.pk),
             "may_read_sheet": draft.activity_id is not None and a.file_extension.lower() in paper_sheets.SCAN_EXTENSIONS}
            for a in draft.attachments.all()]


def day_choices(activity: Activity | None) -> list[tuple[str, str]]:
    return [(day.isoformat(), long_date(day)) for day in activity_days(activity)] if activity else []


def draft_page(member, draft: ReportDraft, version_number: str | None) -> dict:
    versions = list(draft.versions.order_by("number"))
    latest = versions[-1] if versions else None
    shown = next((v for v in versions if str(v.number) == str(version_number)), latest) if versions else None
    messages = list(draft.messages.select_related("version").order_by("created_at", "message_id"))
    busy = any(v.state in (ReportDraftVersion.State.PENDING, ReportDraftVersion.State.RUNNING) for v in versions)
    return {
        "draft": draft, "versions": versions, "shown": version_view(member, shown, shown == latest) if shown else None,
        "messages": [{"message": m, "mine": m.role == ReportChatMessage.Role.USER} for m in messages],
        "busy": busy, "attachments": attachment_rows(draft), "days": day_choices(draft.activity),
        "sheet_kinds": PaperSheetRead.SheetKind.choices, "attachment_kinds": ReportAttachment.Kind.choices,
        "message_max": settings.REPORT_GENERATOR_MESSAGE_MAX, "max_files": settings.REPORT_GENERATOR_MAX_ATTACHMENTS,
        "examples": EXAMPLE_REQUESTS, "ai_off": not gemini.is_configured(),
        "scope": scope_text(draft),
    }


def sheet_page(sheet: PaperSheetRead) -> dict:
    rows = list(sheet.rows.select_related("participant_category").order_by("row_number", "row_id"))
    editable = sheet.state in (PaperSheetRead.State.READ, PaperSheetRead.State.FAILED)
    back = (reverse("reports:generator_draft", args=[sheet.attachment.draft_id]) if sheet.attachment_id
            else reverse("documents:detail", args=[sheet.document_id]))
    return {
        "sheet": sheet, "rows": rows, "editable": editable, "tone": SHEET_TONES[sheet.state],
        "mix": paper_sheets.sheet_mix(sheet) if rows else None, "categories": list(ParticipantCategory.objects.order_by("name")),
        "confirm_refused": paper_sheets.confirm_refused_reason(sheet) if sheet.state == PaperSheetRead.State.READ else None,
        "file_name": sheet.document.title if sheet.document_id else sheet.attachment.file_name,
        "back_url": back, "poll_seconds": POLL_SECONDS,
        "day": long_date(sheet.day) if sheet.day else "",
        "rules": settings.PAPER_SHEET_CATEGORY_RULES,
    }
