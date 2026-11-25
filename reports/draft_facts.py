"""The facts of a report draft (ASSUMPTIONS 442-445): EVERY number, date, name list and amount the generator prints,
taken from the database and the generated forms at the moment a version is written, never from the model's text.

A version stores them (REPORT_DRAFT_VERSION.facts, plain JSON), so a version always prints what it showed. The AI only
writes the narrative sections and puts figures in as [[placeholders]] from `placeholders` (reports.generator fills them).
Numbers come from reports.metrics, the budget from activities.budget, the name lists from registrations.attendance_forms,
the photos from documents (Event Photos) and registrations.evaluation_photos.photos_for_reports (ticked files only).
"""

from datetime import date, datetime

from django.conf import settings
from django.utils import timezone

from accounts.models import TBIMember
from activities import budget as activity_budget
from activities.formatting import format_money, format_time_range
from activities.models import Activity
from activities.services import objective_lines
from documents.models import Document
from registrations import attendance, attendance_forms
from registrations.evaluation_photos import photos_for_reports

from . import metrics
from .content import range_in_words
from .models import PaperSheetRead, ReportAttachment, ReportDraft

IMAGE_EXTENSIONS = ("jpg", "png")


def percent(fraction: float | None, digits: int = 1) -> str:
    """0.825 -> "82.5%"; None -> "—" (nothing to divide by)."""
    return "—" if fraction is None else f"{fraction * 100:.{digits}f}%"


def mean_text(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def long_date(value: date) -> str:
    return attendance_forms.long_date(value)


def prepared_by(member: TBIMember) -> dict:
    """The member generating the report with their POSITION (item 429)."""
    return attendance_forms.prepared_by(member)


def noted_by() -> dict:
    """"Noted:" is always the TBI Director (owner decision): the active signatory linked to an active Director's account
    (item 437; Role.DIRECTOR only says WHO, never a permission). Its name and the position on the signatory list; empty
    name (a blank line to sign) when no Director has a linked signature."""
    from certificates.services import signatory_for
    from notifications.services import active_directors

    for director in active_directors():
        signatory = signatory_for(director)
        if signatory is not None:
            return {"name": signatory.name, "position": signatory.position or "TBI Director"}
    return {"name": "", "position": "TBI Director"}


def _mix(mix: dict) -> dict:
    basis = mix["basis"]
    rows = [{"group": row["group"], "count": row[basis] if basis else 0,
             "share": percent(row[f"{basis}_share"], 0) if basis else "—"} for row in mix["rows"]]
    check = mix.get("check")
    return {
        "basis": basis or "", "total": mix[basis] if basis else 0, "rows": rows,
        "checked": bool(mix.get("checked")), "within": None if check is None else check["within"],
        "problems": [] if check is None else check["problems"], "guideline": metrics.guideline_text(),
    }


def _photos(draft: ReportDraft, activities: list[Activity]) -> list[dict]:
    """Event Photos documents (JPG / PNG) of the activities, the draft's own photo attachments, then the participants'
    photos staff ticked "Use in reports" (photos_for_reports: never any other participant file)."""
    photos = [{"source": "document", "id": document.pk, "caption": document.title}
              for document in Document.objects.activity_documents().filter(
                  activity__in=activities, document_type__name=settings.EVENT_PHOTOS_TYPE,
                  file_extension__in=IMAGE_EXTENSIONS).order_by("uploaded_at", "pk")]
    photos += [{"source": "attachment", "id": attachment.pk, "caption": ""}
               for attachment in draft.attachments.filter(kind=ReportAttachment.Kind.PHOTO,
                                                          file_extension__in=IMAGE_EXTENSIONS)]
    photos += [{"source": "evaluation", "id": photo.pk, "caption": "Photo from a participant"}
               for photo in photos_for_reports([a.pk for a in activities])
               if photo.content_type in ("image/jpeg", "image/png")]
    return photos


def paper_sheets(activity: Activity) -> list[dict]:
    """The activity's CONFIRMED paper sheets (ASSUMPTIONS 443: rows count nowhere before staff confirm them), each with
    its included rows and its mix ("From the paper sheet"; never added to REGISTRATION)."""
    result = []
    for sheet in (PaperSheetRead.objects.filter(activity=activity, state=PaperSheetRead.State.CONFIRMED)
                  .order_by("day", "read_id")):
        rows = list(sheet.rows.filter(is_excluded=False).select_related("participant_category")
                    .order_by("row_number", "row_id"))
        names = [row.participant_category.name if row.participant_category_id else None for row in rows]
        result.append({
            "read_id": sheet.pk, "kind": sheet.get_sheet_kind_display(),
            "day": long_date(sheet.day) if sheet.day else "",
            "rows": [{"number": number, "name": row.name, "designation": row.designation, "office": row.office,
                      "category": row.participant_category.name if row.participant_category_id else ""}
                     for number, row in enumerate(rows, start=1)],
            "mix": _mix(metrics.paper_sheet_mix(names)),
        })
    return result


def _annex(activity: Activity) -> dict:
    """The name lists of the generated forms (item 434) and the activity's Attendance Sheet / Confirmation of Attendance
    documents (the PDF annex appends the newest of each)."""
    def newest(type_name: str) -> int | None:
        document = (Document.objects.activity_documents().filter(activity=activity, document_type__name=type_name)
                    .order_by("-uploaded_at", "-pk").first())
        return document.pk if document else None

    return {
        "attendance_pages": [{"date": page["date"], "rows": [
            {key: row[key] for key in ("number", "name", "designation", "office", "time", "walk_in")}
            for row in page["rows"]]} for page in attendance_forms.attendance_pages(activity)],
        "confirmation_rows": [{key: row[key] for key in ("number", "name", "designation", "will_attend")}
                              for row in attendance_forms.confirmation_rows(activity)],
        "attendance_document_id": newest("Attendance Sheet"),
        "confirmation_document_id": newest(settings.CONFIRMATION_OF_ATTENDANCE),
    }


def _budget(activity: Activity) -> dict:
    summary = activity_budget.budget_summary(activity)
    return {
        "fund_source": summary["fund_source"], "fund_account": summary["fund_account"],
        "rows": [{"particulars": row["particulars"], "quantity": row["quantity"],
                  "unit_cost": format_money(row["unit_cost"]), "total": format_money(row["total_amount"])}
                 for row in summary["rows"]],
        "total": format_money(summary["total"]),
        "note": "Actual spending is reconciled with the liquidation.",
    }


def _ratings(ratings: list[dict]) -> list[dict]:
    return [{"question": item["question"], "answers": item["answers"], "mean": mean_text(item["mean"]),
             "frequency": [{"rating": f["rating"], "count": f["count"], "share": percent(f["share"])}
                           for f in item["frequency"]]} for item in ratings]


def activity_header(activity: Activity, options: dict) -> dict:
    return {
        "title": activity.title,
        "office_line": options.get("office_line", ""),
        "period_covered": attendance_forms.date_span(activity.start_date, activity.end_date),
        "time": format_time_range(activity.start_time, activity.end_time),
        "venue": activity.venue,
        "implementing_unit": options.get("implementing_unit") or settings.REPORT_IMPLEMENTING_UNIT,
        "project_leader": options.get("project_leader") or activity.created_by.name,
        "activity_type": activity.activity_type.name,
    }


def activity_facts(draft: ReportDraft, member: TBIMember) -> dict:
    """Everything the Activity Report prints (ASSUMPTIONS 444)."""
    activity = draft.activity
    counts = metrics.activity_registration_counts(activity)
    evaluation = metrics.activity_evaluation_summary(activity)
    days = attendance.checkins_by_day(activity)
    header = activity_header(activity, draft.options)
    people = [{"name": link.resource_person.name, "topic": link.topic}
              for link in activity.resource_person_links.select_related("resource_person").order_by("pk")]
    budget = _budget(activity)
    facts = {
        "header": header,
        "objectives": objective_lines(activity),
        "description": activity.description or "",
        "resource_persons": people,
        "counts": {**{key: counts[key] for key in ("registered", "cancelled", "walk_ins", "attended")},
                   "attendance_rate": percent(counts["attendance_rate"]), "evaluations": evaluation["evaluations"],
                   "response_rate": percent(evaluation["evaluation_response_rate"])},
        "days": [{"number": number, "date": long_date(day["day"]), "attended": day["total"]}
                 for number, day in enumerate(days, start=1)],
        "mix": _mix(metrics.participant_mix([activity.pk])[activity.pk]),
        "budget": budget,
        "photos": _photos(draft, [activity]),
        "annex": _annex(activity),
        "paper_sheets": paper_sheets(activity),
    }
    placeholders = {
        "title": header["title"], "period_covered": header["period_covered"], "venue": header["venue"],
        "implementing_unit": header["implementing_unit"], "project_leader": header["project_leader"],
        "registered": str(counts["registered"]), "attended": str(counts["attended"]),
        "walk_ins": str(counts["walk_ins"]), "cancelled": str(counts["cancelled"]),
        "attendance_rate": facts["counts"]["attendance_rate"], "evaluations": str(evaluation["evaluations"]),
        "response_rate": facts["counts"]["response_rate"], "budget_total": budget["total"],
        "number_of_days": str(len(days)), "resource_persons_count": str(len(people)),
    }
    for day in facts["days"]:
        placeholders[f"day_{day['number']}_date"] = day["date"]
        placeholders[f"day_{day['number']}_attended"] = str(day["attended"])
    facts["placeholders"] = placeholders
    return facts


def evaluation_facts(draft: ReportDraft, member: TBIMember) -> dict:
    """The Evaluation Summary (ASSUMPTIONS 445): per activity or per period, numbers from reports.metrics only. The open
    answers are NOT stored here (they go only into the prompt, without names: evaluation_texts)."""
    if draft.activity_id:
        activity = draft.activity
        data = metrics.activity_evaluation_summary(activity)
        header = activity_header(activity, draft.options)
        facts = {
            "header": header,
            "scope": "activity",
            "counts": {"registered": data["registered"], "cancelled": data["cancelled"], "attended": data["attended"],
                       "evaluations": data["evaluations"],
                       "response_rate": percent(data["evaluation_response_rate"])},
            "ratings": _ratings(data["ratings"]),
            "open_answers": len(evaluation_texts(draft)),
            "activities": [],
            "photos": _photos(draft, [activity]),
        }
    else:
        filters = metrics.ReportFilters(draft.date_range_start, draft.date_range_end)
        summary = metrics.period_summary(filters)
        rows = metrics.activity_rows(filters)
        facts = {
            "header": {"title": draft.title, "period_covered": range_in_words(filters.start, filters.end),
                       "implementing_unit": draft.options.get("implementing_unit") or settings.REPORT_IMPLEMENTING_UNIT,
                       "office_line": draft.options.get("office_line", "")},
            "scope": "period",
            "counts": {"activities": summary["activities_conducted"], "registered": summary["registered"],
                       "cancelled": summary["cancelled"], "attended": summary["attended"],
                       "evaluations": summary["evaluations"],
                       "response_rate": percent(summary["evaluation_response_rate"]),
                       "average_rating": mean_text(summary["average_rating"])},
            "ratings": _ratings(metrics.period_rating_frequency(filters)),
            "open_answers": len(evaluation_texts(draft)),
            "activities": [{"title": row["title"], "dates": attendance_forms.date_span(row["start_date"], row["end_date"]),
                            "attended": row["attended"], "evaluations": row["evaluations"],
                            "response_rate": percent(metrics.rate(row["evaluations"], row["attended"])),
                            "average_rating": mean_text(row["average_rating"])} for row in rows],
            "photos": _photos(draft, list(metrics.period_activities(filters))),
        }
    counts = facts["counts"]
    facts["placeholders"] = {
        "title": facts["header"]["title"], "period_covered": facts["header"]["period_covered"],
        **{key: str(value) for key, value in counts.items()},
        **{f"question_{n}_mean": item["mean"] for n, item in enumerate(facts["ratings"], start=1)},
    }
    return facts


def evaluation_texts(draft: ReportDraft) -> list[str]:
    """The written answers and comments for the AI summary: never names (metrics returns them sorted A-Z)."""
    if draft.activity_id:
        data = metrics.activity_evaluation_summary(draft.activity)
        return [text for item in data["text_answers"] for text in item["answers"]] + data["comments"]
    return metrics.period_text_answers(metrics.ReportFilters(draft.date_range_start, draft.date_range_end))


def other_facts(draft: ReportDraft, member: TBIMember) -> dict:
    """A free-form report: the activity's or the period's key figures (the AI may only use these as placeholders)."""
    if draft.activity_id:
        facts = activity_facts(draft, member)
        facts["annex"] = {"attendance_pages": [], "confirmation_rows": [], "attendance_document_id": None,
                          "confirmation_document_id": None}
        return facts
    filters = metrics.ReportFilters(draft.date_range_start, draft.date_range_end)
    summary = metrics.period_summary(filters)
    counts = {"activities": summary["activities_conducted"], "registered": summary["registered"],
              "cancelled": summary["cancelled"], "attended": summary["attended"],
              "attendance_rate": percent(summary["attendance_rate"]), "evaluations": summary["evaluations"],
              "budget_total": format_money(metrics.period_budget_total(filters))}
    header = {"title": draft.title, "period_covered": range_in_words(filters.start, filters.end),
              "implementing_unit": draft.options.get("implementing_unit") or settings.REPORT_IMPLEMENTING_UNIT,
              "office_line": draft.options.get("office_line", "")}
    return {
        "header": header, "counts": counts,
        "activities": [{"title": row["title"], "dates": attendance_forms.date_span(row["start_date"], row["end_date"]),
                        "attended": row["attended"]} for row in metrics.activity_rows(filters)],
        "photos": _photos(draft, list(metrics.period_activities(filters))),
        "placeholders": {"title": header["title"], "period_covered": header["period_covered"],
                         **{key: str(value) for key, value in counts.items()}},
    }


def build_facts(draft: ReportDraft, member: TBIMember, now: datetime | None = None) -> dict:
    """The facts of a new version, by kind, plus who prepared it and who notes it."""
    builder = {ReportDraft.Kind.ACTIVITY_REPORT: activity_facts, ReportDraft.Kind.EVALUATION_SUMMARY: evaluation_facts,
               ReportDraft.Kind.OTHER: other_facts}[draft.kind]
    facts = builder(draft, member)
    facts["prepared_by"] = prepared_by(member)
    facts["noted_by"] = noted_by()
    facts["form_code"] = settings.ACTIVITY_REPORT_FORM_CODE if draft.kind == ReportDraft.Kind.ACTIVITY_REPORT else ""
    facts["collected_at"] = timezone.localtime(now or timezone.now()).isoformat()
    return facts
