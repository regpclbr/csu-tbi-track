"""The office documents of the defense / testing mock data (`seed_defense`, ASSUMPTIONS 470).

Real text PDFs on the CSU letterhead (config/letterhead.py), one template per document type
(templates/defense/document_pdf.html), filled from one activity's details: Activity Design, Request Letter (memorandum
with a document tracking number and its QR code), Invitation Letter (to a resource person), Pre-registration Form,
Evaluation Form and "Other" (the Program of Activities). The Attendance Sheet and the Confirmation of Attendance are the
system's own generated forms (registrations.attendance_forms) and the Activity Report is the report generator's
(reports.generator), so they are not here.

Every person and organization is invented; "SAMPLE – FOR SYSTEM DEMONSTRATION" is printed in the footer of every page
whatever LETTERHEAD_SAMPLE_LINE says, so a printed copy can never pass for a real CSU document. Rendered through
certificates.pdf.get_renderer() (WeasyPrint; the fake renderer in tests).
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.template.loader import render_to_string

from activities.formatting import format_money
from config import letterhead

SAMPLE_TRACKING_PREFIX = "TBI"


@dataclass(frozen=True)
class Person:
    name: str
    position: str


@dataclass(frozen=True)
class ResourcePerson:
    name: str
    topic: str
    title: str  # "Founder and CEO"
    organization: str
    salutation: str  # "Ms. Reyes"


@dataclass
class Details:
    """What the documents print about one activity (all invented for the mock data)."""

    title: str
    tbi: str
    type_name: str
    start: date
    end: date
    start_time: time
    end_time: time
    venue: str
    categories: list[str]
    objectives: list[str]
    description: str
    fund_source: str  # "RAF – Regular Agency Fund"
    fund_account: str
    budget: list[tuple[str, str, int, str, int, str]]  # (category, particulars, quantity, unit, days, unit price)
    max_slots: int
    prepared_by: Person
    director: Person
    vice_president: Person
    president: Person
    resource_persons: list[ResourcePerson] = field(default_factory=list)


def long_date(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def date_span(start: date, end: date) -> str:
    if start == end:
        return long_date(start)
    if (start.year, start.month) == (end.year, end.month):
        return f"{start:%B} {start.day}–{end.day}, {end.year}"
    return f"{long_date(start)} – {long_date(end)}"


def clock(value: time) -> str:
    return f"{value.hour % 12 or 12}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def _days(details: Details) -> int:
    return (details.end - details.start).days + 1


def budget_rows(details: Details) -> tuple[list[dict], str]:
    """(rows, the total) with the same arithmetic as activities.budget.line_total (quantity × days × unit price)."""
    rows, total = [], Decimal("0")
    for _category, particulars, quantity, unit, days, price in details.budget:
        amount = Decimal(price) * quantity * days
        total += amount
        days_text = f" × {days} day{'s' if days != 1 else ''}" if days != 1 else ""
        rows.append({"particulars": particulars, "quantity": f"{quantity} {unit}{days_text}",
                     "unit_cost": format_money(Decimal(price)), "total": format_money(amount)})
    return rows, format_money(total)


def _split(begin: datetime, finish: datetime, parts: int) -> list[tuple[datetime, datetime]]:
    """`parts` back-to-back slots from begin to finish, each a whole number of 5 minutes."""
    total = int((finish - begin).total_seconds() // 60)
    step = max(5, (total // max(1, parts)) // 5 * 5)
    edges = [begin + timedelta(minutes=step * index) for index in range(parts)] + [finish]
    return list(zip(edges[:-1], edges[1:]))


def program(details: Details) -> list[dict]:
    """A realistic time table per day, from the activity's start / end time and its resource persons' topics: opening
    (day 1) or recap, the sessions (split around a lunch break when the day spans noon), a workshop, the open forum and
    the closing."""
    sessions_all = [f"{person.topic} – {person.name}" for person in details.resource_persons] or [
        f"Session: {details.objectives[0]}" if details.objectives else "Main session"]
    total_days = _days(details)
    days = []
    for number in range(total_days):
        day = details.start + timedelta(days=number)
        start = datetime.combine(day, details.start_time)
        end = datetime.combine(day, details.end_time)
        if end - start < timedelta(hours=2):
            end = start + timedelta(hours=4)
        slots: list[tuple[str, str]] = []

        def add(begin: datetime, finish: datetime, text: str) -> None:
            slots.append((f"{clock(begin.time())} – {clock(finish.time())}", text))

        if number == 0:
            add(start - timedelta(minutes=30), start, "Registration of Participants (QR check-in)")
            opening = f"Opening Program and Welcome Remarks – {details.director.name}, {details.director.position}"
        else:
            opening = f"Day {number + 1} Opening and Recap"
        add(start, start + timedelta(minutes=30), opening)
        cursor = start + timedelta(minutes=30)
        topics = sessions_all[number::total_days] or sessions_all[-1:]
        items = [*topics, "Workshop: application to the participants' own startups and enterprises"]
        closing_start = end - timedelta(minutes=60)
        noon, after_lunch = day_time(day, 12), day_time(day, 13)
        if cursor < noon and closing_start > after_lunch + timedelta(minutes=30):
            morning, afternoon = items[: max(1, len(items) // 2)], items[max(1, len(items) // 2):]
            for (begin, finish), text in zip(_split(cursor, noon, len(morning)), morning):
                add(begin, finish, text)
            add(noon, after_lunch, "Lunch Break")
            for (begin, finish), text in zip(_split(after_lunch, closing_start, len(afternoon)), afternoon):
                add(begin, finish, text)
        else:
            for (begin, finish), text in zip(_split(cursor, closing_start, len(items)), items):
                add(begin, finish, text)
        add(closing_start, end - timedelta(minutes=30), "Open Forum and Synthesis")
        last = number == total_days - 1
        add(end - timedelta(minutes=30), end,
            "Closing Program, Evaluation and Awarding of Certificates" if last else f"End of Day {number + 1}")
        days.append({"heading": f"Day {number + 1} | {long_date(day)}", "slots": slots})
    return days


def day_time(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour, 0))


def _qr(text: str) -> str:
    import segno

    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="png", scale=6, border=2)
    return f"data:image/png;base64,{base64.b64encode(out.getvalue()).decode('ascii')}"


def _event_rows(details: Details) -> list[tuple[str, str]]:
    return [("Title of the Activity", details.title), ("Date", date_span(details.start, details.end)),
            ("Time", f"{clock(details.start_time)} – {clock(details.end_time)}"), ("Venue", details.venue)]


def _render(kind: str, doc_title: str, context: dict, office_line: str = "") -> bytes:
    from certificates.pdf import get_renderer

    html = render_to_string("defense/document_pdf.html", {
        "kind": kind, "doc_title": doc_title,
        "letterhead": letterhead.context(office_line=office_line, sample=True), **context})
    return get_renderer().render(html)


def joined(names: list[str]) -> str:
    """"Faculty, Incubatees and MSMEs"."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _participants(details: Details) -> str:
    joined_names = joined(details.categories)
    return (f"{details.max_slots} participants: {joined_names}, selected through the online pre-registration of the CSU-TBI "
            f"Track system. Faculty take at most 30% and incubatees at most 70% of the participants (NAVIGATU's "
            f"guideline).")


def activity_design(details: Details) -> bytes:
    rows, total = budget_rows(details)
    duration = f"{_days(details)} day{'s' if _days(details) != 1 else ''}"
    context = {
        "summary_rows": [("Title", details.title), ("Duration", duration),
                         ("Date", date_span(details.start, details.end)),
                         ("Time", f"{clock(details.start_time)} – {clock(details.end_time)}"),
                         ("Venue", details.venue), ("Participants", joined(details.categories)),
                         ("Funds", f"{details.fund_source} – {details.fund_account}")],
        "rationale": [
            f"Caraga State University, through its Technology Business Incubator ({details.tbi}), supports student, "
            f"faculty and community innovators in turning research outputs and business ideas into viable, "
            f"market-ready startups and stronger micro, small and medium enterprises in the Caraga Region.",
            details.description,
            "The activity responds to the needs raised by the incubatees and partner MSMEs during the TBI's mentoring "
            "sessions and consultations, and supports the University's commitment to innovation, technology transfer "
            "and extension services.",
        ],
        "objectives": details.objectives, "participants_text": _participants(details),
        "program": program(details), "resource_persons": details.resource_persons,
        "budget_rows": rows, "budget_total": total, "fund_source": details.fund_source,
        "fund_account": details.fund_account,
        "expected_outputs": [f"{details.max_slots} participants trained and their attendance recorded",
                             "Evaluation results and recommendations of the participants",
                             "Workshop outputs of the participating startups and enterprises",
                             "Activity Report with photo documentation for the Supply Office"],
        "signatures": [{"label": "Prepared by:", "name": details.prepared_by.name, "position": details.prepared_by.position},
                       {"label": "Recommending Approval:", "name": details.director.name,
                        "position": details.director.position},
                       {"label": "Approved by:", "name": details.president.name, "position": details.president.position}],
    }
    return _render("activity_design", f"Activity Design – {details.title}", context)


def request_letter(details: Details, tracking_number: str, letter_date: date) -> bytes:
    rows, total = budget_rows(details)
    context = {
        "memo": [("FOR", details.president), ("THRU", details.vice_president), ("FROM", details.director)],
        "subject": f"REQUEST FOR THE CONDUCT OF THE {details.title.upper()} – {date_span(details.start, details.end).upper()}",
        "letter_date": long_date(letter_date),
        "body": [
            f"Caraga State University, through its Technology Business Incubator ({details.tbi}), respectfully requests "
            f"your approval for the conduct of the {details.title} on {date_span(details.start, details.end)}, "
            f"{clock(details.start_time)} to {clock(details.end_time)}, at the {details.venue}.",
            f"{details.description} The activity is open to {joined(details.categories)}, with "
            f"{details.max_slots} slots for participants who register through the CSU-TBI Track system.",
            f"In this regard, may we also request the approval of the budget amounting to {total}, charged to the "
            f"{details.fund_source} ({details.fund_account}), as detailed in the attached Activity Design.",
            "For any inquiries or further coordination, please feel free to reach out to the TBI Office.",
        ],
        "signatures": [{"label": "Very truly yours,", "name": details.director.name, "position": details.director.position},
                       {"label": "Recommending Approval:", "name": details.vice_president.name,
                        "position": details.vice_president.position},
                       {"label": "Approved by:", "name": details.president.name, "position": details.president.position}],
        "tracking_number": tracking_number, "qr": _qr(tracking_number),
    }
    return _render("request_letter", f"Request Letter – {details.title}", context)


def invitation_letter(details: Details, person: ResourcePerson, tracking_number: str, letter_date: date) -> bytes:
    context = {
        "letter_date": long_date(letter_date),
        "addressee": {"name": person.name, "title": person.title, "organization": person.organization,
                      "salutation": person.salutation},
        "body": [
            f"Greetings from the Technology Business Incubator ({details.tbi}) of Caraga State University!",
            f"We are pleased to invite you as a resource speaker in the {details.title} on "
            f"{date_span(details.start, details.end)}, {clock(details.start_time)} to {clock(details.end_time)}, at the "
            f"{details.venue}. We would be honored if you could share your expertise on \"{person.topic}\".",
            f"The activity is for {joined(details.categories)} of the Caraga Region. "
            f"{details.description}",
            "Your honorarium, meals and local transportation are covered by the activity's approved budget. Kindly "
            "confirm your availability and send us your presentation and a short profile three days before the "
            "activity.",
            "Thank you, and we look forward to your favorable response.",
        ],
        "signatures": [{"label": "", "name": details.director.name, "position": details.director.position}],
        "tracking_number": tracking_number, "qr": _qr(tracking_number),
    }
    return _render("invitation_letter", f"Invitation Letter – {details.title}", context)


def pre_registration_form(details: Details, extra_fields: list[str]) -> bytes:
    fields = ["Full name (as it should appear on the certificate)", "Designation / Position", "Office / Agency",
              "Email address", "Contact number", *extra_fields]
    return _render("pre_registration_form", f"Pre-registration Form – {details.title}",
                   {"event_rows": _event_rows(details), "fields": fields, "categories": details.categories})


def evaluation_form(details: Details, ratings: list[str], open_questions: list[str]) -> bytes:
    return _render("evaluation_form", f"Evaluation Form – {details.title}",
                   {"event_rows": _event_rows(details), "ratings": ratings, "open_questions": open_questions})


def program_of_activities(details: Details) -> bytes:
    return _render("other", f"Program of Activities – {details.title}", {
        "heading": "PROGRAM OF ACTIVITIES", "event_rows": _event_rows(details), "program": program(details),
        "signatures": [{"label": "Prepared by:", "name": details.prepared_by.name, "position": details.prepared_by.position},
                       {"label": "Noted:", "name": details.director.name, "position": details.director.position}]})


def memo_document(heading: str, paragraphs: list[str], prepared_by: Person, noted_by: Person) -> bytes:
    """A short report or request on the letterhead (the files the mock data submits to the Director)."""
    return _render("memo", heading.title(), {
        "heading": heading.upper(), "body": paragraphs,
        "signatures": [{"label": "Prepared by:", "name": prepared_by.name, "position": prepared_by.position},
                       {"label": "Noted:", "name": noted_by.name, "position": noted_by.position}]})


def tracking_number(year: int, sequence: int) -> str:
    """"TBI-2026-00132" (the office's document tracking number format)."""
    return f"{SAMPLE_TRACKING_PREFIX}-{year}-{sequence:05d}"
