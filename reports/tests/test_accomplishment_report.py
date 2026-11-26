"""The Accomplishment Report in the report generator (ASSUMPTIONS 446): a quarter, a half or a whole year (the Reports
page's presets), NAVIGATU's "3rd Quarter Accomplishment Report" layout, every number from reports.metrics (cancelled
registrations not counted, item 440), confirmed paper Attendance sheets replacing the system's attendance per DAY (item
447), the photo rules of the Activity Report, DOCX / PDF, no save to documents. Fake Gemini and a fake renderer only; no
test depends on today's date (presets take the year, versions take `now`)."""

import io
import json
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from activities.models import ParticipantCategory
from documents import services as document_services
from documents.models import DocumentType
from registrations.models import EvaluationAttachment, EvaluationSubmission, FormTemplate, FormType, Registration
from reports import draft_export, draft_facts, generator, metrics
from reports.content import period_name
from reports.models import PaperSheetRead, PaperSheetRow, ReportAttachment, ReportDraft, ReportDraftVersion
from reports.services import preset_range

from .conftest import TEST_NOW
from .test_generator import blank_pdf, png, renderer, reply, staff, workshop, write  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db
Kind = ReportDraft.Kind
Q4 = ("q4", 2026)


def accomplishment(member, preset=Q4, program="NAVIGATU", **extra) -> ReportDraft:
    start, end = generator.accomplishment_dates(*preset)
    return generator.create_draft(member, kind=Kind.ACCOMPLISHMENT_REPORT, start=start, end=end,
                                  options={"program_name": program, **extra.pop("options", {})}, now=TEST_NOW, **extra)


def sections_for(version_facts: dict, **overrides) -> dict:
    """A full AI reply for an Accomplishment Report (one entry per section of its facts)."""
    text = {key: f"Text for {label}." for key, label, _ in generator.section_specs(Kind.ACCOMPLISHMENT_REPORT, version_facts)}
    for key in text:
        if key.endswith(("_accomplishments", "_objectives")) or key in ("outputs", "recommendations"):
            text[key] = "First item\nSecond item"
    text.update(overrides)
    return text


def write_with(draft: ReportDraft, fake_gemini, **overrides) -> ReportDraftVersion:
    """Write version 1: the facts first (to know the per-activity keys), then a full reply."""
    facts = draft_facts.build_facts(draft, draft.created_by, TEST_NOW)
    fake_gemini.replies.append(reply(**sections_for(facts, **overrides)))
    version = generator.latest_version(draft)
    generator.generate_version(version.pk, now=TEST_NOW)
    version.refresh_from_db()
    return version


@pytest.fixture
def clinic(make_activity):
    """A second Conducted Q4 activity with Objectives (item 428) and nobody registered."""
    activity = make_activity("Mentoring Clinic", start=date(2026, 11, 5), categories=("Incubatees",))
    activity.objectives = "Match incubatees with mentors\nReview business models"
    activity.save(update_fields=["objectives"])
    return activity


@pytest.fixture
def q3_activity(make_activity):
    return make_activity("Summer Bootcamp", start=date(2026, 8, 5))


# --- The period ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key, start, end, heading, covered", [
    ("q1", date(2026, 1, 1), date(2026, 3, 31), "1ST QUARTER ACCOMPLISHMENT REPORT", "January–March 2026"),
    ("q2", date(2026, 4, 1), date(2026, 6, 30), "2ND QUARTER ACCOMPLISHMENT REPORT", "April–June 2026"),
    ("q3", date(2026, 7, 1), date(2026, 9, 30), "3RD QUARTER ACCOMPLISHMENT REPORT", "July–September 2026"),
    ("q4", date(2026, 10, 1), date(2026, 12, 31), "4TH QUARTER ACCOMPLISHMENT REPORT", "October–December 2026"),
    ("h1", date(2026, 1, 1), date(2026, 6, 30), "SEMI-ANNUAL ACCOMPLISHMENT REPORT", "January–June 2026"),
    ("h2", date(2026, 7, 1), date(2026, 12, 31), "SEMI-ANNUAL ACCOMPLISHMENT REPORT", "July–December 2026"),
    ("year", date(2026, 1, 1), date(2026, 12, 31), "ANNUAL ACCOMPLISHMENT REPORT", "January–December 2026"),
])
def test_quarter_half_and_year_presets_and_their_dates(key, start, end, heading, covered) -> None:
    assert generator.accomplishment_dates(key, 2026) == (start, end) == preset_range(key, 2026)
    assert generator.accomplishment_dates(key, "2026") == (start, end)  # the form sends text
    assert draft_facts.accomplishment_heading(start, end) == heading
    assert draft_facts.months_covered(start, end) == covered
    assert period_name(start, end) is not None


def test_only_a_preset_period_and_never_an_activity(staff, workshop) -> None:
    for preset, year in (("", 2026), ("q5", 2026), ("q1", "x"), ("q1", 1900)):
        with pytest.raises(ValidationError) as error:
            generator.accomplishment_dates(preset, year)
        assert error.value.message_dict == {"preset": [generator.PRESET_REQUIRED]}
    with pytest.raises(ValidationError) as error:  # a custom range is not a quarter, half or year
        generator.create_draft(staff, kind=Kind.ACCOMPLISHMENT_REPORT, start="2026-10-01", end="2026-10-31",
                               options={"program_name": "NAVIGATU"})
    assert "preset" in error.value.message_dict
    with pytest.raises(ValidationError) as error:
        generator.create_draft(staff, kind=Kind.ACCOMPLISHMENT_REPORT, activity_id=workshop.pk,
                               options={"program_name": "NAVIGATU"})
    assert error.value.message_dict == {"activity": [generator.PERIOD_ONLY]}


def test_the_program_name_is_required_and_remembered(staff) -> None:
    with pytest.raises(ValidationError) as error:
        accomplishment(staff, program="  ")
    assert error.value.message_dict == {"program_name": [generator.PROGRAM_REQUIRED]}
    accomplishment(staff, program="TARA")
    draft = accomplishment(staff, preset=("h2", 2026), program="NAVIGATU")
    assert draft.title == "Semi-annual Accomplishment Report - July to December 2026 – NAVIGATU"
    assert generator.last_options(staff, Kind.ACCOMPLISHMENT_REPORT) == {"program_name": "NAVIGATU"}


# --- Numbers from the database ---------------------------------------------------------------------------------------------


def test_numbers_come_from_the_database_and_cancelled_are_not_registered(staff, workshop, clinic, q3_activity,
                                                                        fake_gemini) -> None:
    draft = accomplishment(staff)
    version = write_with(draft, fake_gemini, introduction=(
        "The program held [[activities_conducted]] activities with [[attended]] participants; "
        f"the workshop had [[act{workshop.pk}_attended]] attendees on [[act{workshop.pk}_dates]]."))
    facts = version.facts
    filters = metrics.ReportFilters(date(2026, 10, 1), date(2026, 12, 31))
    summary = metrics.period_summary(filters)
    # Ana, Ben, Dan registered and attended; Carla cancelled ("Will not attend", item 440): never counted as registered.
    assert facts["counts"]["registered"] == summary["registered"] == 3
    assert facts["counts"]["cancelled"] == summary["cancelled"] == 1
    assert facts["counts"]["attended"] == summary["attended"] == 3
    assert facts["counts"]["activities_conducted"] == summary["activities_conducted"] == 2
    assert [item["title"] for item in facts["activities"]] == ["Pitching Workshop", "Mentoring Clinic"]  # not Q3's
    assert facts["counts"]["budget_total"] == "₱10,000.00"  # 9,000 + the clinic's 1,000 (metrics.period_budget_total)
    assert sum(Decimal(row["total"].replace("₱", "").replace(",", "")) for row in facts["budget_by_fund_source"]) == 10000
    text = version.sections["introduction"]["text"]
    assert text == ("The program held 2 activities with 3 participants; the workshop had 3 attendees on "
                    "October 20–21, 2026.")
    assert version.sections["introduction"]["unverified"] == [] and facts["attendance_note"] == ""
    assert facts["header"] == {"heading": "4TH QUARTER ACCOMPLISHMENT REPORT", "program_name": "NAVIGATU",
                               "office_line": "", "period_covered": "October–December 2026",
                               "implementing_unit": facts["header"]["implementing_unit"]}
    assert facts["form_code"].startswith("F-COR-001 Rev. 3")


def test_a_number_the_ai_wrote_in_an_activity_section_blocks_export(staff, workshop, clinic, fake_gemini) -> None:
    draft = accomplishment(staff)
    version = write_with(draft, fake_gemini, **{f"activity_{workshop.pk}_highlights": "About 45 people came."})
    assert version.sections[f"activity_{workshop.pk}_highlights"]["unverified"] == ["45"]
    assert "Check the numbers" in generator.export_refused_reason(version)
    edited = generator.edit_section(staff, version, f"activity_{workshop.pk}_highlights", "Many people came.")
    assert generator.export_refused_reason(edited) is None


def test_the_counts_match_the_reports_page_without_paper_sheets(staff, workshop, clinic) -> None:
    """Parity with the Reports page's Accomplishment report (the same metrics): per activity and in total."""
    filters = metrics.ReportFilters(date(2026, 10, 1), date(2026, 12, 31))
    rows = {row["activity_id"]: row for row in metrics.activity_rows(filters)}
    adjusted = metrics.attendance_with_paper_sheets(list(rows))
    assert {pk: value["attended"] for pk, value in adjusted.items()} == {pk: row["attended"] for pk, row in rows.items()}
    facts = draft_facts.build_facts(accomplishment(staff), staff, TEST_NOW)
    assert facts["counts"]["attended"] == facts["attended_system"] == metrics.period_summary(filters)["attended"]
    assert facts["mix"]["rows"] == draft_facts._mix(metrics.period_participant_mix(filters))["rows"]


# --- Paper sheets (per day, attendance only) -----------------------------------------------------------------------------


def _confirmed_sheet(activity, member, kind, day, rows) -> PaperSheetRead:
    type_name = "Attendance Sheet" if kind == PaperSheetRead.SheetKind.ATTENDANCE else "Confirmation of Attendance"
    document = document_services.upload_document(member, activity, DocumentType.objects.get(name=type_name),
                                                 SimpleUploadedFile("sheet.pdf", blank_pdf()), "Scan")
    sheet = PaperSheetRead.objects.create(activity=activity, document=document, sheet_kind=kind, day=day,
                                          state=PaperSheetRead.State.CONFIRMED, read_by=member, confirmed_by=member,
                                          confirmed_at=TEST_NOW)
    for number, (name, office, category, excluded) in enumerate(rows, start=1):
        PaperSheetRow.objects.create(sheet=sheet, row_number=number, name=name, office=office, is_excluded=excluded,
                                     participant_category=ParticipantCategory.objects.get(name=category) if category else None)
    return sheet


def test_a_paper_attendance_sheet_replaces_the_systems_attendance_for_its_day(staff, director, workshop, clinic,
                                                                              fake_gemini, renderer) -> None:
    # Day 1 (Oct 20) on paper: Ana (same name + office as her registration: one person), Eve, Fe; one excluded row.
    _confirmed_sheet(workshop, director, PaperSheetRead.SheetKind.ATTENDANCE, date(2026, 10, 20), [
        ("ANA  reyes", "dti", "Incubatees", False), ("Eve Tan", "CSU", "Faculty", False),
        ("Fe Go", "Startup X", "Incubatees", False), ("Heading row", "", None, True)])
    # A Confirmation of Attendance sheet means "will attend": it never replaces attendees.
    _confirmed_sheet(workshop, director, PaperSheetRead.SheetKind.CONFIRMATION, date(2026, 10, 21), [
        ("Zed Uy", "LGU", "MSMEs", False)])
    # A sheet still being checked (not confirmed) is never used.
    unconfirmed = _confirmed_sheet(workshop, director, PaperSheetRead.SheetKind.ATTENDANCE, date(2026, 10, 21), [
        ("Not Yet", "X", "MSMEs", False)])
    unconfirmed.state, unconfirmed.confirmed_by, unconfirmed.confirmed_at = PaperSheetRead.State.READ, None, None
    unconfirmed.save()

    result = metrics.attendance_with_paper_sheets([workshop.pk])[workshop.pk]
    # Day 1 from paper: Ana, Eve, Fe (Ben, who only came on day 1 in the system, is replaced); day 2 from the system:
    # Ana (already counted, matched by name + office) and Dan.
    assert result["attended"] == 4 and result["attended_system"] == 3 and result["paper_days"] == 1
    assert [(d["source"], d["count"]) for d in result["days"]] == [("paper", 3), ("system", 2)]
    assert sorted(result["categories"]) == ["Faculty", "Incubatees", "Incubatees", "MSMEs"]

    version = write_with(accomplishment(staff), fake_gemini)
    facts = version.facts
    workshop_item = facts["activities"][0]
    assert workshop_item["attended"] == 4 and workshop_item["attended_system"] == 3
    assert workshop_item["days"] == ["Day 1 (October 20, 2026): 3 – attendance from the paper sheet",
                                     "Day 2 (October 21, 2026): 2 – attendance from the system"]
    assert facts["counts"]["attended"] == 4 and facts["attended_system"] == 3
    # The Reports page counts the system only: the difference is expected and the report says why.
    assert metrics.period_summary(metrics.ReportFilters(date(2026, 10, 1), date(2026, 12, 31)))["attended"] == 3
    assert "the system alone recorded 3" in facts["attendance_note"]
    assert {row["group"]: row["count"] for row in facts["mix"]["rows"]} == {"Faculty": 1, "Incubatees": 2, "MSMEs": 1}
    generator.export_pdf(staff, version)
    html = renderer.rendered[-1]
    assert "attendance from the paper sheet" in html and "the system alone recorded 3" in html


def test_a_sheet_without_a_day_counts_only_for_a_one_day_activity(staff, director, workshop, clinic) -> None:
    _confirmed_sheet(clinic, director, PaperSheetRead.SheetKind.ATTENDANCE, None, [("Ivy Ko", "Startup Y", "Incubatees", False)])
    _confirmed_sheet(workshop, director, PaperSheetRead.SheetKind.ATTENDANCE, None, [("Jo Lu", "Z", "MSMEs", False)])
    result = metrics.attendance_with_paper_sheets([clinic.pk, workshop.pk])
    assert result[clinic.pk]["attended"] == 1 and result[clinic.pk]["days"][0]["source"] == "paper"
    assert result[workshop.pk]["attended"] == 3 and result[workshop.pk]["paper_days"] == 0  # two days: no day, not used


# --- Objectives, photos, exports --------------------------------------------------------------------------------------------


def test_objectives_from_the_field_or_read_from_the_activity_design(staff, workshop, clinic, fake_gemini, renderer) -> None:
    draft = accomplishment(staff)
    facts = draft_facts.build_facts(draft, staff, TEST_NOW)
    keys = [key for key, _, _ in generator.section_specs(Kind.ACCOMPLISHMENT_REPORT, facts)]
    assert f"activity_{workshop.pk}_objectives" in keys  # no Objectives field: the AI reads its Activity Design
    assert f"activity_{clinic.pk}_objectives" not in keys  # item 428's field is used as it is
    assert keys[0] == "introduction" and keys[-3:] == ["outputs", "problems", "recommendations"]
    version = write_with(draft, fake_gemini, **{f"activity_{workshop.pk}_objectives": "Teach pitching\nPractice"})
    generator.export_pdf(staff, version)
    html = renderer.rendered[-1]
    assert "B. Accomplishments vis-à-vis Objectives" in html and "Read from the Activity Design." in html
    assert "Teach pitching" in html and "Match incubatees with mentors" in html
    blocks = draft_export.section_blocks(version)
    rows = draft_export.accomplishment_rows(version, blocks)
    assert [row["objectives_read"] for row in rows] == [True, False]
    assert rows[0]["accomplishments"] == ["First item", "Second item"]


def test_photos_follow_the_activity_report_rules(staff, workshop, clinic, fake_gemini, renderer) -> None:
    registration = Registration.objects.get(name="Ana Reyes")
    form = FormTemplate.objects.create(activity=workshop, form_type=FormType.EVALUATION, title="Eval",
                                       created_by=workshop.created_by)
    submission = EvaluationSubmission.objects.create(template=form, registration=registration, certificate_name="Ana",
                                                     certificate_email="ana@example.com")
    ticked, unticked = [EvaluationAttachment.objects.create(
        submission=submission, file_path=ContentFile(png(), name=f"evaluation-photos/a{n}.png"), file_name=f"a{n}.png",
        file_size_kb=1, content_type="image/png", use_in_reports=use, uploaded_at=TEST_NOW,
        marked_at=TEST_NOW if use else None) for n, use in ((1, True), (2, False))]
    photos = [generator.NewFile(SimpleUploadedFile(f"g{n}.png", png()), ReportAttachment.Kind.PHOTO) for n in range(6)]
    version = write_with(accomplishment(staff, files=photos), fake_gemini)
    sources = [(p["source"], p["id"]) for p in version.facts["photos"]]
    assert ("evaluation", ticked.pk) in sources and ("evaluation", unticked.pk) not in sources and len(sources) == 7
    generator.export_pdf(staff, version)
    assert renderer.rendered[-1].count("Photo Documentation") == 2  # 6 a page
    prompt = fake_gemini.generate_calls[-1]["turns"][0].text
    assert "g0.png" not in prompt and not any(name in prompt for name in ("Ana Reyes", "Ben Cruz", "Dan Uy"))


def test_the_docx_and_the_pdf_open(staff, director, workshop, clinic, fake_gemini, renderer) -> None:
    from docx import Document as Docx

    version = write_with(accomplishment(staff, options={"office_line": "Office of the VP for RDIE"}), fake_gemini)
    data, name = generator.export_docx(staff, version)
    assert name.endswith("-v1.docx")
    document = Docx(io.BytesIO(data))
    text = "\n".join(p.text for p in document.paragraphs)
    cells = "\n".join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
    assert "4TH QUARTER ACCOMPLISHMENT REPORT" in text and "NAVIGATU" in text
    assert "Period Covered: October–December 2026" in text
    assert "B. Accomplishments vis-à-vis Objectives" in text and "E. Recommendations / Next Steps" in text
    assert "Pitching Workshop" in cells and "Prepared by:" in cells and "Noted:" in cells
    assert document.sections[0].footer.paragraphs[0].text.startswith("F-COR-001 Rev. 3")
    assert generator.AI_LABEL not in text + cells
    pdf, _ = generator.export_pdf(staff, version)
    assert pdf.startswith(b"%PDF-")
    html = renderer.rendered[-1]
    for part in ("4TH QUARTER ACCOMPLISHMENT REPORT", "Period Covered: October–December 2026", "A.1 Pitching Workshop",
                 "C. Major Outputs / Deliverables", "D. Problems / Constraints Met", "F-COR-001 Rev. 3",
                 "Office of the VP for RDIE"):
        assert part in html
    assert generator.AI_LABEL not in html


def test_without_the_key_the_figures_are_still_there(staff, workshop, clinic, monkeypatch, settings, renderer) -> None:
    from assistant import gemini

    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = ""
    draft = accomplishment(staff)
    version = generator.latest_version(draft)
    generator.generate_version(version.pk, now=TEST_NOW)
    version.refresh_from_db()
    assert version.state == ReportDraftVersion.State.READY and version.ai_prompt is None
    assert all(value["text"] == "" for value in version.sections.values())
    assert f"activity_{workshop.pk}_highlights" in version.sections
    assert version.facts["counts"]["attended"] == 3
    assert generator.export_docx(staff, version)[0][:2] == b"PK"
    assert generator.export_pdf(staff, version)[0].startswith(b"%PDF-")


def test_a_period_report_is_submitted_but_never_saved(staff, director, workshop, clinic, fake_gemini, renderer) -> None:
    version = write_with(accomplishment(staff), fake_gemini)
    with pytest.raises(generator.GeneratorError, match="one activity"):
        generator.save_to_documents(staff, version)
    submission = generator.submit_to_director(staff, version)
    assert submission.activity is None and submission.document_type is None


# --- Pages ----------------------------------------------------------------------------------------------------------------


def test_creating_an_accomplishment_report_through_the_page(staff, workshop, client, clock, django_capture_on_commit_callbacks) -> None:
    clock(timezone.make_aware(timezone.datetime(2026, 11, 26, 9, 0)))
    client.force_login(staff)
    page = client.get(reverse("reports:generator") + "?start_kind=accomplishment_report", follow=True)
    assert page.redirect_chain[-1][0].endswith("?kind=accomplishment_report")  # the one Reports page (461)
    assert page.status_code == 200 and "Quarter, half or year" in page.content.decode()
    refused = client.post(reverse("reports:generator"), {"kind": "accomplishment_report", "preset": "", "year": "2026",
                                                         "program_name": "NAVIGATU"})
    assert refused.status_code == 400 and generator.PRESET_REQUIRED in refused.content.decode()
    with django_capture_on_commit_callbacks():
        response = client.post(reverse("reports:generator"), {"kind": "accomplishment_report", "preset": "q3",
                                                              "year": "2026", "program_name": "TARA"})
    draft = ReportDraft.objects.get()
    assert response.status_code == 302 and (draft.date_range_start, draft.date_range_end) == (date(2026, 7, 1), date(2026, 9, 30))
    assert draft.options["program_name"] == "TARA"
    assert 'value="TARA"' in client.get(reverse("reports:list") + "?kind=accomplishment_report").content.decode()
    figures = client.get(reverse("reports:list")).content.decode()  # the figures Accomplishment report links here (446)
    assert "data-accomplishment-note" in figures and "?kind=accomplishment_report" in figures
