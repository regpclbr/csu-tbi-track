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
from .money_words import pesos_in_words
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


BUDGET_NOTE = ("The amounts presented reflect the budget allocation indicated in the approved activity design. Actual "
               "expenditures should be reconciled with the corresponding financial documents and liquidation records.")


def _budget(activity: Activity) -> dict:
    summary = activity_budget.budget_summary(activity)
    return {
        "fund_source": summary["fund_source"], "fund_account": summary["fund_account"],
        "rows": [{"particulars": row["particulars"], "quantity": row["quantity"],
                  "unit_cost": format_money(row["unit_cost"]), "total": format_money(row["total_amount"])}
                 for row in summary["rows"]],
        "total": format_money(summary["total"]),
        # "Twenty-Seven Thousand Pesos" (ASSUMPTIONS 469), only when there is a budget; the sentence is printed by the
        # template, never written by the AI.
        "total_words": pesos_in_words(summary["total"]) if summary["rows"] and summary["total"] else "",
        "note": BUDGET_NOTE,
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


# --- The Accomplishment Report (ASSUMPTIONS 446) -----------------------------------------------------------------------

ORDINALS = {1: "1ST", 2: "2ND", 3: "3RD", 4: "4TH"}


def accomplishment_heading(start: date, end: date) -> str:
    """"3RD QUARTER ACCOMPLISHMENT REPORT" / "SEMI-ANNUAL ACCOMPLISHMENT REPORT" / "ANNUAL ACCOMPLISHMENT REPORT"
    (NAVIGATU's sample), from the dates (a preset, reports.content.period_name)."""
    from .content import HALF, QUARTER, period_name

    name = period_name(start, end)
    if name is not None and name.kind == QUARTER:
        return f"{ORDINALS[(start.month - 1) // 3 + 1]} QUARTER ACCOMPLISHMENT REPORT"
    if name is not None and name.kind == HALF:
        return "SEMI-ANNUAL ACCOMPLISHMENT REPORT"
    return "ANNUAL ACCOMPLISHMENT REPORT"


ORDINAL_WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth"}


def accomplishment_period_phrase(start: date, end: date) -> str:
    """How section C names the period: "the third quarter", "the first half of the year", "the year" (ASSUMPTIONS 469)."""
    from .content import HALF, QUARTER, period_name

    name = period_name(start, end)
    if name is not None and name.kind == QUARTER:
        return f"the {ORDINAL_WORDS[(start.month - 1) // 3 + 1]} quarter"
    if name is not None and name.kind == HALF:
        return f"the {'first' if start.month == 1 else 'second'} half of the year"
    return "the year"


def months_covered(start: date, end: date) -> str:
    """"July–September 2026" (a preset always covers whole months of one year)."""
    if (start.year, start.month) == (end.year, end.month):
        return f"{start:%B} {start.year}"
    return f"{start:%B}–{end:%B} {end.year}"


def _day_label(number: int, day: dict) -> str:
    source = "from the paper sheet" if day["source"] == metrics.PAPER else "from the system"
    return f"Day {number} ({long_date(day['day'])}): {day['count']} – attendance {source}"


def accomplishment_facts(draft: ReportDraft, member: TBIMember) -> dict:
    """Everything the Accomplishment Report prints for its quarter / half / year: the Conducted activities ending in the
    period (reports.metrics: the Reports page's own rule), their registered (cancelled not counted, item 440) and
    attended people (confirmed paper Attendance sheets replace the system's attendance for their day: metrics
    .attendance_with_paper_sheets), the participant mix over those people, the evaluation results, the budget per fund
    source and the photos (the Activity Report's photo rules)."""
    filters = metrics.ReportFilters(draft.date_range_start, draft.date_range_end)
    summary = metrics.period_summary(filters)
    rows = metrics.activity_rows(filters)
    activities = {activity.pk: activity for activity in metrics.period_activities(filters)
                  .select_related("activity_type", "fund_source")}
    attendance_by_activity = metrics.attendance_with_paper_sheets(list(activities))
    items, placeholders, all_categories = [], {}, []
    for number, row in enumerate(rows, start=1):
        activity = activities[row["activity_id"]]
        attendance = attendance_by_activity[activity.pk]
        all_categories += attendance["categories"]
        prefix = f"act{activity.pk}"
        people = [{"name": link.resource_person.name, "topic": link.topic}
                  for link in activity.resource_person_links.select_related("resource_person").order_by("pk")]
        item = {
            "number": number, "activity_id": activity.pk, "title": activity.title,
            "type": row["activity_type__name"], "categories": row["categories"],
            "dates": attendance_forms.date_span(activity.start_date, activity.end_date),
            "time": format_time_range(activity.start_time, activity.end_time), "venue": activity.venue,
            "registered": row["registered"], "cancelled": row["cancelled"], "attended": attendance["attended"],
            "attended_system": attendance["attended_system"], "paper_days": attendance["paper_days"],
            "days": [_day_label(n, day) for n, day in enumerate(attendance["days"], start=1)],
            "evaluations": row["evaluations"], "average_rating": mean_text(row["average_rating"]),
            "objectives": objective_lines(activity), "description": activity.description or "",
            "resource_persons": people, "budget_total": format_money(activity.budget_amount),
            "fund_source": activity_budget.fund_source_text(activity),
        }
        values = {"title": item["title"], "dates": item["dates"], "venue": item["venue"],
                  "categories": item["categories"], "registered": str(item["registered"]),
                  "attended": str(item["attended"]), "evaluations": str(item["evaluations"]),
                  "average_rating": item["average_rating"], "budget_total": item["budget_total"]}
        item["placeholder_names"] = [f"[[{prefix}_{key}]]" for key in values]
        placeholders.update({f"{prefix}_{key}": value for key, value in values.items()})
        items.append(item)
    attended = sum(item["attended"] for item in items)
    paper_activities = [item for item in items if item["paper_days"]]
    if paper_activities:
        mix = _mix(metrics.paper_sheet_mix(all_categories, checked=False))
    else:
        mix = _mix(metrics.period_participant_mix(filters))
    note = ""
    if paper_activities:
        note = (f"Attended includes the confirmed paper attendance sheets of {len(paper_activities)} "
                f"activit{'y' if len(paper_activities) == 1 else 'ies'} (they replace the system's attendance for "
                f"their day); the system alone recorded {summary['attended']}. The Reports page counts the system's "
                f"attendance only.")
    budget_rows = [{"fund_source": row["fund_source"], "activities": row["activities"], "total": format_money(row["total"])}
                   for row in metrics.budget_by_fund_source(filters)]
    budget_total = format_money(metrics.period_budget_total(filters))
    start, end = filters.start, filters.end
    header = {
        "heading": accomplishment_heading(start, end),
        "period_phrase": accomplishment_period_phrase(start, end),
        "program_name": draft.options.get("program_name", ""),
        "office_line": draft.options.get("office_line", ""),
        "period_covered": months_covered(start, end),
        "implementing_unit": draft.options.get("implementing_unit") or settings.REPORT_IMPLEMENTING_UNIT,
    }
    counts = {"activities_conducted": summary["activities_conducted"], "registered": summary["registered"],
              "cancelled": summary["cancelled"], "attended": attended, "evaluations": summary["evaluations"],
              "response_rate": percent(summary["evaluation_response_rate"]),
              "average_rating": mean_text(summary["average_rating"]), "budget_total": budget_total}
    facts = {
        "header": header, "counts": counts, "attended_system": summary["attended"], "attendance_note": note,
        "activities": items, "mix": mix, "ratings": _ratings(metrics.period_rating_frequency(filters)),
        "budget_by_fund_source": budget_rows,
        "photos": _photos(draft, list(activities.values())),
    }
    placeholders.update({
        "program_name": header["program_name"], "period_covered": header["period_covered"],
        "implementing_unit": header["implementing_unit"],
        **{key: str(value) for key, value in counts.items()},
        **{f"budget_{row['code'].lower()}": format_money(row["total"])
           for row in metrics.budget_by_fund_source(filters) if row["code"]},
    })
    facts["placeholders"] = placeholders
    return facts


# --- The draft Request Letter and Activity Design (ASSUMPTIONS 447) ---------------------------------------------------


def _typed_time(value: str):
    from datetime import time

    return time.fromisoformat(value) if value else None


def _days_text(start: date, end: date) -> str:
    days = (end - start).days + 1
    return f"{days} day{'s' if days != 1 else ''}"


def proposal_details(draft: ReportDraft) -> dict:
    """The activity a draft letter / design is about: the chosen Draft / Scheduled activity's own fields (read again
    for every version), or what the member typed (REPORT_DRAFT.options["proposal"]). Budget lines: activities.budget's
    table either way (the typed ones are checked again by clean_lines; an inactive category may stay)."""
    from activities.models import FundSource

    if draft.activity_id:
        activity = draft.activity
        categories = ", ".join(activity.participant_categories.order_by("name").values_list("name", flat=True))
        summary = activity_budget.budget_summary(activity)
        return {
            "source": "activity", "title": activity.title, "start_date": activity.start_date,
            "end_date": activity.end_date, "start_time": activity.start_time, "end_time": activity.end_time,
            "venue": activity.venue,
            "participants": f"{categories} (up to {activity.max_slots} participants)" if categories
            else f"Up to {activity.max_slots} participants",
            "objectives": objective_lines(activity), "description": activity.description or "",
            "fund_source": summary["fund_source"], "fund_account": activity.fund_account or "",
            "rows": summary["rows"], "total": summary["total"],
            "resource_persons": [{"name": link.resource_person.name, "topic": link.topic} for link in
                                 activity.resource_person_links.select_related("resource_person").order_by("pk")],
        }
    typed = draft.options.get("proposal") or {}
    raw_lines = typed.get("budget_lines") or []
    lines = activity_budget.clean_lines(raw_lines, allowed_inactive={line["category"] for line in raw_lines})
    table = activity_budget.budget_table(None, lines)
    fund_source = FundSource.objects.filter(pk=typed.get("fund_source_id")).first() if typed.get("fund_source_id") else None
    return {
        "source": "typed", "title": typed.get("title", ""), "start_date": date.fromisoformat(typed["start_date"]),
        "end_date": date.fromisoformat(typed["end_date"]), "start_time": _typed_time(typed.get("start_time", "")),
        "end_time": _typed_time(typed.get("end_time", "")), "venue": typed.get("venue", ""),
        "participants": typed.get("participants", ""),
        "objectives": [line for line in (typed.get("objectives") or "").splitlines() if line.strip()],
        "description": typed.get("description", ""),
        "fund_source": str(fund_source) if fund_source else activity_budget.NOT_SET, "fund_account": "",
        "rows": table["rows"], "total": table["total"], "resource_persons": [],
    }


def proposal_facts(draft: ReportDraft, member: TBIMember, now: datetime | None = None) -> dict:
    """The draft Request Letter / Activity Design (ASSUMPTIONS 447): every date, name and amount from the activity or
    from what the member typed; the memorandum's names and titles typed by the member (blank lines when empty)."""
    details = proposal_details(draft)
    options = draft.options
    dates = attendance_forms.date_span(details["start_date"], details["end_date"])
    time_text = format_time_range(details["start_time"], details["end_time"])
    budget = {
        "fund_source": details["fund_source"], "fund_account": details["fund_account"],
        "rows": [{"particulars": row["particulars"], "quantity": row["quantity"],
                  "unit_cost": format_money(row["unit_cost"]), "total": format_money(row["total_amount"])}
                 for row in details["rows"]],
        "total": format_money(details["total"]),
    }
    funds = f"{budget['total']} – {budget['fund_source']}" if details["rows"] else budget["fund_source"]
    proposal = {
        "title": details["title"], "duration": _days_text(details["start_date"], details["end_date"]),
        "dates": dates, "time": time_text, "venue": details["venue"], "participants": details["participants"],
        "funds": funds, "objectives": details["objectives"], "description": details["description"],
        "source": details["source"],
    }
    director = noted_by()
    today = long_date(timezone.localdate(now or timezone.now()))
    facts = {
        "header": {"title": details["title"], "office_line": options.get("office_line", "")},
        "proposal": proposal, "budget": budget, "resource_persons": details["resource_persons"],
        "date": today,
    }
    if draft.kind == ReportDraft.Kind.REQUEST_LETTER:
        memo = {
            "for": {"name": options.get("memo_for_name", ""), "title": options.get("memo_for_title", "")},
            "thru": {"name": options.get("memo_thru_name", ""), "title": options.get("memo_thru_title", "")},
            "from": {"name": options.get("memo_from_name") or options.get("project_leader") or director["name"],
                     "title": options.get("memo_from_title") or director["position"]},
            "subject": f"Request for the conduct of {details['title']}",
            "date": today,
        }
        facts["memo"] = memo
        facts["signatures"] = [
            {"label": "Recommending Approval:", "name": memo["thru"]["name"], "position": memo["thru"]["title"]},
            {"label": "Approved by:", "name": memo["for"]["name"], "position": memo["for"]["title"]},
        ]
    else:
        prepared = prepared_by(member)
        facts["signatures"] = [
            {"label": "Prepared by:", "name": prepared["name"], "position": prepared["position"]},
            {"label": "Recommending Approval:", "name": director["name"], "position": director["position"]},
            {"label": "Approved by:", "name": options.get("approved_by_name", ""),
             "position": options.get("approved_by_title", "")},
        ]
    facts["placeholders"] = {
        "title": details["title"], "dates": dates, "time": time_text, "venue": details["venue"],
        "participants": details["participants"], "duration": proposal["duration"],
        "budget_total": budget["total"], "fund_source": budget["fund_source"], "date_today": today,
    }
    return facts


def build_facts(draft: ReportDraft, member: TBIMember, now: datetime | None = None) -> dict:
    """The facts of a new version, by kind, plus who prepared it and who notes it."""
    kind = draft.kind
    if kind in (ReportDraft.Kind.REQUEST_LETTER, ReportDraft.Kind.ACTIVITY_DESIGN):
        facts = proposal_facts(draft, member, now)
    else:
        builder = {ReportDraft.Kind.ACTIVITY_REPORT: activity_facts, ReportDraft.Kind.EVALUATION_SUMMARY: evaluation_facts,
                   ReportDraft.Kind.ACCOMPLISHMENT_REPORT: accomplishment_facts, ReportDraft.Kind.OTHER: other_facts}[kind]
        facts = builder(draft, member)
    facts["prepared_by"] = prepared_by(member)
    facts["noted_by"] = noted_by()
    facts["form_code"] = (settings.ACTIVITY_REPORT_FORM_CODE
                          if kind in (ReportDraft.Kind.ACTIVITY_REPORT, ReportDraft.Kind.ACCOMPLISHMENT_REPORT) else "")
    facts["collected_at"] = timezone.localtime(now or timezone.now()).isoformat()
    return facts
