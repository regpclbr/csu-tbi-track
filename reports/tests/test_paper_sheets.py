"""Paper attendance sheets read by the AI (ASSUMPTIONS 443): the rows need staff confirmation before they are used, only
in the generated reports ("From the paper sheet"), never in REGISTRATION; offices mapped to categories (DTI and partner
agencies count as MSMEs, pending NAVIGATU). Fake Gemini only."""

import json
import logging
from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from activities.models import ParticipantCategory
from documents import services as document_services
from documents.models import Document, DocumentType
from registrations.models import Registration
from reports import draft_facts, metrics, paper_sheets
from reports.models import PaperSheetRead, ReportAttachment, ReportDraft

from .test_generator import blank_pdf, png

pytestmark = pytest.mark.django_db
State = PaperSheetRead.State
ROWS = {"rows": [
    {"name": "Ana Reyes", "designation": "Owner", "office": "Reyes Trading"},
    {"name": "Ben Cruz", "designation": "Instructor", "office": "Caraga State University"},
    {"name": "Carla Lim", "designation": "Trade Specialist", "office": "DTI Agusan del Norte"},
    {"name": "Dino Uy", "designation": "Founder", "office": "TBI Incubatee - AgriTech"},
    {"name": "", "designation": "", "office": "Barangay Hall"},
]}


@pytest.fixture
def activity(make_activity):
    return make_activity("Pitching Workshop", start=date(2026, 10, 20), end=date(2026, 10, 21),
                         categories=("Faculty", "Incubatees", "MSMEs"))


@pytest.fixture
def scan(activity, director) -> Document:
    return document_services.upload_document(director, activity, DocumentType.objects.get(name="Attendance Sheet"),
                                             SimpleUploadedFile("sheet.pdf", blank_pdf()), "Scanned attendance")


def read(sheet: PaperSheetRead) -> PaperSheetRead:
    paper_sheets.read_sheet(sheet.pk)
    sheet.refresh_from_db()
    return sheet


def test_reading_a_scanned_document_ticks_it_and_needs_the_flag(scan, director, view_only, monkeypatch) -> None:
    queued = []
    monkeypatch.setattr("django_q.tasks.async_task", lambda *a, **k: queued.append(a))
    with pytest.raises(PermissionDenied):
        paper_sheets.start_from_document(view_only, scan, "attendance")
    sheet = paper_sheets.start_from_document(director, scan, "attendance", "2026-10-20")
    scan.refresh_from_db()
    assert sheet.state == State.READING and sheet.day == date(2026, 10, 20)
    assert scan.ai_scan_allowed_by == director  # the click is the per-file consent (item 438)


def test_only_attendance_scans_and_activity_days(activity, director, scan) -> None:
    letter = document_services.upload_document(director, activity, DocumentType.objects.get(name="Request Letter"),
                                               SimpleUploadedFile("letter.pdf", blank_pdf()), "Letter")
    with pytest.raises(paper_sheets.PaperSheetError, match="Attendance Sheet or a Confirmation"):
        paper_sheets.start_from_document(director, letter, "attendance")
    with pytest.raises(ValidationError, match="activity's days"):
        paper_sheets.start_from_document(director, scan, "attendance", "2026-12-25")


def test_the_ai_reads_the_rows_and_our_code_maps_the_offices(scan, director, fake_gemini, caplog) -> None:
    fake_gemini.ocr_text = json.dumps(ROWS)
    with caplog.at_level(logging.INFO):
        sheet = read(paper_sheets.start_from_document(director, scan, "attendance"))
    assert sheet.state == State.READ
    rows = {row.name: (row.participant_category.name if row.participant_category else None, row.category_by_staff)
            for row in sheet.rows.all()}
    assert rows == {"Ana Reyes": ("MSMEs", False), "Ben Cruz": ("Faculty", False), "Carla Lim": ("MSMEs", False),
                    "Dino Uy": ("Incubatees", False), "": (None, False)}
    assert fake_gemini.read_calls[0]["mime_type"] == "application/pdf"
    assert "Ana Reyes" not in caplog.text  # never names in the logs


def test_nothing_is_used_until_staff_confirm(scan, director, fake_gemini, activity) -> None:
    fake_gemini.ocr_text = json.dumps(ROWS)
    sheet = read(paper_sheets.start_from_document(director, scan, "attendance"))
    assert draft_facts.paper_sheets(activity) == []  # read, not confirmed: used nowhere
    with pytest.raises(paper_sheets.PaperSheetError, match="rows 5"):
        paper_sheets.confirm(director, sheet)  # an empty row without a name / category
    empty = sheet.rows.get(name="")
    paper_sheets.save_rows(director, sheet, [{"row_id": empty.pk, "name": "", "designation": "", "office": "",
                                              "category_id": "", "excluded": True}])
    paper_sheets.confirm(director, sheet)
    [used] = draft_facts.paper_sheets(activity)
    assert [row["name"] for row in used["rows"]] == ["Ana Reyes", "Ben Cruz", "Carla Lim", "Dino Uy"]
    shares = {row["group"]: (row["count"], row["share"]) for row in used["mix"]["rows"]}
    assert shares == {"Faculty": (1, "25%"), "Incubatees": (1, "25%"), "MSMEs": (2, "50%")}
    assert not Registration.objects.filter(activity=activity).exists()  # never added to REGISTRATION
    assert metrics.activity_registration_counts(activity)["registered"] == 0  # the dashboard numbers do not change


def test_staff_correct_rows_and_a_confirmed_sheet_is_locked(scan, director, fake_gemini) -> None:
    fake_gemini.ocr_text = json.dumps(ROWS)
    sheet = read(paper_sheets.start_from_document(director, scan, "attendance"))
    ana = sheet.rows.get(name="Ana Reyes")
    incubatees = ParticipantCategory.objects.get(name="Incubatees")
    changed = paper_sheets.save_rows(director, sheet, [{"row_id": ana.pk, "name": "Ana M. Reyes", "designation": "Owner",
                                                        "office": "Reyes Trading", "category_id": incubatees.pk}])
    ana.refresh_from_db()
    assert changed == 1 and ana.name == "Ana M. Reyes" and ana.category_by_staff and ana.edited_by == director
    paper_sheets.add_row(director, sheet)
    for row in sheet.rows.filter(name=""):
        row.is_excluded = True
        row.save()
    paper_sheets.confirm(director, sheet)
    with pytest.raises(paper_sheets.PaperSheetError, match="Reopen"):
        paper_sheets.save_rows(director, sheet, [])
    paper_sheets.reopen(director, sheet)
    assert PaperSheetRead.objects.get(pk=sheet.pk).confirmed_at is None


def test_without_the_key_the_sheet_fails_and_rows_can_be_typed(scan, director, monkeypatch, settings) -> None:
    from assistant import gemini

    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = ""
    sheet = read(paper_sheets.start_from_document(director, scan, "attendance"))
    assert sheet.state == State.FAILED and sheet.error == paper_sheets.AI_OFF
    paper_sheets.add_row(director, sheet)
    assert PaperSheetRead.objects.get(pk=sheet.pk).state == State.READ


def test_an_unreadable_answer_fails(scan, director, fake_gemini) -> None:
    fake_gemini.ocr_text = "I see a table with names."
    assert read(paper_sheets.start_from_document(director, scan, "attendance")).error == paper_sheets.NOT_READABLE


def test_a_draft_attachment_can_be_read(activity, director, fake_gemini, monkeypatch) -> None:
    from reports import generator

    monkeypatch.setattr("django_q.tasks.async_task", lambda *a, **k: None)
    draft = generator.create_draft(director, kind=ReportDraft.Kind.ACTIVITY_REPORT, activity_id=activity.pk, files=[
        generator.NewFile(SimpleUploadedFile("sheet.png", png()), ReportAttachment.Kind.DOCUMENT)])
    fake_gemini.ocr_text = json.dumps(ROWS)
    sheet = read(paper_sheets.start_from_attachment(director, draft.attachments.get(), "confirmation"))
    assert sheet.rows.count() == 5 and fake_gemini.read_calls[0]["mime_type"] == "image/png"


def test_the_category_rules(settings, roles) -> None:
    assert paper_sheets.suggest_category("DTI Agusan").name == "MSMEs"
    assert paper_sheets.suggest_category("Caraga State University - CCIS").name == "Faculty"
    assert paper_sheets.suggest_category("Educsu Inc.") is None  # whole words only ("csu" inside a word is not CSU)
    assert paper_sheets.suggest_category("") is None


def test_the_review_page(scan, director, fake_gemini, client) -> None:
    fake_gemini.ocr_text = json.dumps(ROWS)
    sheet = read(paper_sheets.start_from_document(director, scan, "attendance"))
    client.force_login(director)
    html = client.get(reverse("reports:paper_sheet", args=[sheet.pk])).content.decode()
    assert "Confirm the rows" in html and "Ana Reyes" in html and "Counts and percentages" in html
    html = client.get(reverse("documents:detail", args=[scan.pk])).content.decode()
    assert "Read the rows of this paper sheet" in html and reverse("reports:paper_sheet", args=[sheet.pk]) in html


def test_paper_sheet_mix_metric() -> None:
    mix = metrics.paper_sheet_mix(["Faculty", "Incubatees", "MSMEs", "MSMEs", None])
    assert mix["attended"] == 4 and mix["no_category"] == 1 and mix["check"]["within"] is True
