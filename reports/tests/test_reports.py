"""Generated reports: requesting (permissions, validation), the background build (PDF and Excel),
state, the AI narrative, formula injection and downloads."""

import json
import zipfile
from datetime import date, datetime
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_q.models import Task
from openpyxl import load_workbook

from assistant.gemini import AssistantUnavailable
from reports import services, tasks
from reports.content import AI_LABEL, REPORT_TYPES
from reports.models import GeneratedReport
from startups.models import Startup
from startups.services import STARTUP_SESSION_KEY

from .conftest import TEST_NOW

pytestmark = pytest.mark.django_db

FORMULA_TITLE = '=HYPERLINK("https://evil.example","Click me")'


@pytest.fixture
def queued(monkeypatch) -> list[int]:
    """Reports queued for the background task (nothing is really sent to Django-Q here)."""
    calls: list[int] = []
    monkeypatch.setattr(services, "queue_build", calls.append)
    return calls


@pytest.fixture
def year_data(make_activity, register, evaluate):
    """One conducted activity with registrations, an evaluation (with personal text) and a startup."""
    startup = Startup.objects.create(name="Secret Startup Inc", email="founder@secret.test", navigatu_id="S1")
    activity = make_activity("Pitching Workshop", start=date(2026, 3, 10), categories=("Faculty", "MSMEs"))
    ana = register(activity, "Ana Dela Cruz", attended=True, email="ana.private@example.com", startup=startup)
    register(activity, "Ben Santos", attended=False, email="ben.private@example.com")
    evaluate(ana, {"Overall rating": 5}, comments="Ana's private comment", text_answers={"Suggestions": "Call me at 0917"})
    return activity


def request(member, **data) -> GeneratedReport:
    payload = {"report_type": "accomplishment", "format": "pdf", "date_range_start": "2026-01-01", "date_range_end": "2026-12-31"}
    payload.update(data)
    return services.request_report(member, payload, now=TEST_NOW)


# --- Requesting -----------------------------------------------------------------------------------


def test_request_needs_can_create_activity(view_only, queued) -> None:
    with pytest.raises(PermissionDenied):
        request(view_only)
    assert not GeneratedReport.objects.exists()


def test_request_saves_the_row_and_queues_the_task_after_commit(director, queued, django_capture_on_commit_callbacks) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        report = request(director, included_sections=["activities", "summary", "summary"])
    assert queued == [report.pk]
    report.refresh_from_db()
    assert json.loads(report.included_sections) == ["summary", "activities"]  # JSON list, report order, no duplicates
    assert report.file_path.name == "" and report.ai_prompt is None
    assert services.report_state(report) == services.PENDING
    assert (report.date_range_start, report.date_range_end, report.format) == (date(2026, 1, 1), date(2026, 12, 31), "pdf")


def test_default_sections(director, queued) -> None:
    report = request(director, report_type="participation", format="excel")
    assert services.sections_of(report) == list(REPORT_TYPES["participation"].default_sections)


def test_request_validation(director, queued) -> None:
    with pytest.raises(ValidationError) as error:
        request(director, report_type="nope", format="word", included_sections=["summary", "salaries"],
                date_range_start="2026-05-01", date_range_end="2026-04-01")
    assert set(error.value.message_dict) == {"report_type", "format", "included_sections", "date_range_end"}


def test_api_request_list_and_detail(director, view_only, login, queued) -> None:
    response = login(director).post(
        reverse("reports:api_reports"),
        data=json.dumps({"report_type": "certificates", "format": "excel", "date_range_start": "2026-01-01", "date_range_end": "2026-06-30"}),
        content_type="application/json",
    )
    assert response.status_code == 202
    report_id = response.json()["report"]["report_id"]
    assert response.json()["report"]["state"] == "pending"
    # Admin Staff cannot generate (403) but can list and read.
    denied = login(view_only).post(reverse("reports:api_reports"), data="{}", content_type="application/json")
    assert denied.status_code == 403
    listing = login(view_only).get(reverse("reports:api_reports")).json()
    assert [r["report_id"] for r in listing["reports"]] == [report_id]
    detail = login(view_only).get(reverse("reports:api_detail", args=[report_id])).json()["report"]
    assert detail["included_sections"] == ["certificates"] and detail["download_url"] is None
    options = login(view_only).get(reverse("reports:api_options")).json()
    assert [t["key"] for t in options["report_types"] if t["main"]] == ["accomplishment", "participation"]


# --- Building ----------------------------------------------------------------------------------------


def test_pdf_report_is_rendered_saved_and_ready(director, queued, year_data, fake_pdf_renderer) -> None:
    report = request(director)
    assert services.build_report(report.pk, now=TEST_NOW) == "generated"
    report.refresh_from_db()
    assert report.file_path.name == f"reports/2026/report-{report.pk}-accomplishment.pdf"
    assert Path(report.file_path.path).read_bytes().startswith(b"%PDF")
    assert services.report_state(report) == services.READY
    text = fake_pdf_renderer.last_text()
    assert "Annual Accomplishment Report - 2026" in text  # a named period (ASSUMPTIONS 399)
    assert "Pitching Workshop" in text and "Activities conducted" in text and "<svg" in fake_pdf_renderer.last
    assert "Ana Dela Cruz" not in text  # reports hold totals and titles, never participant names
    # Building again (a second task) changes nothing.
    assert services.build_report(report.pk) == "already generated"


def test_pdf_escapes_typed_text(director, queued, make_activity, fake_pdf_renderer) -> None:
    make_activity('<script>alert("x")</script> & Co', start=date(2026, 3, 10))
    services.build_report(request(director).pk, now=TEST_NOW)
    assert "<script>" not in fake_pdf_renderer.last
    assert "&lt;script&gt;" in fake_pdf_renderer.last


def test_excel_report_has_real_numbers_formats_and_frozen_headers(director, queued, year_data) -> None:
    report = request(director, format="excel")
    services.build_report(report.pk, now=TEST_NOW)
    report.refresh_from_db()
    assert report.file_path.name.endswith(".xlsx")
    workbook = load_workbook(report.file_path.path)
    assert workbook.sheetnames == ["Report info", "Summary", "Activities", "By type", "By category", "Category per activity", "Per month",
                                   "Budget", "Evaluation"]  # Budget: ASSUMPTIONS 433
    activities = workbook["Activities"]
    assert activities.freeze_panes == "A8"  # the title block (5 rows + 1 blank), then the header on row 7
    header = [cell.value for cell in activities[7]]
    # Every column, also after the PDF combined Start + End into one "Date" column (layout only, ASSUMPTIONS 385).
    assert header == ["Title", "Type", "Categories", "Start", "End", "Time", "Venue", "Registered", "Attended",
                      "Attendance rate", "Evaluations", "Average rating"]
    row = {name: cell for name, cell in zip(header, activities[8])}
    assert row["Title"].value == "Pitching Workshop"
    assert isinstance(row["Start"].value, datetime) and row["Start"].number_format == "mmm d, yyyy"
    assert isinstance(row["End"].value, datetime) and row["Time"].value == "8:00 AM – 5:00 PM"
    assert row["Registered"].value == 2 and isinstance(row["Registered"].value, int)
    assert row["Attendance rate"].value == 0.5 and row["Attendance rate"].number_format == "0.0%"
    assert row["Average rating"].value == 5.0 and row["Average rating"].number_format == "0.00"
    summary = {r[0].value: r[1] for r in workbook["Summary"].iter_rows(min_row=8) if r[0].value}
    assert summary["Attendance rate (attended / registered)"].number_format == "0.0%"


def test_excel_writes_formula_like_titles_as_text(director, queued, make_activity) -> None:
    make_activity(FORMULA_TITLE, start=date(2026, 3, 10))
    make_activity("+cmd|' /C calc'!A0", start=date(2026, 3, 11))
    make_activity("@SUM(1+1)", start=date(2026, 3, 12))
    report = request(director, format="excel", included_sections=["activities"])
    services.build_report(report.pk, now=TEST_NOW)
    report.refresh_from_db()

    sheet = load_workbook(report.file_path.path)["Activities"]
    titles = [sheet.cell(row=row, column=1) for row in range(8, 11)]
    assert [cell.value for cell in titles] == [FORMULA_TITLE, "+cmd|' /C calc'!A0", "@SUM(1+1)"]
    assert all(cell.data_type == "s" for cell in titles)  # text, not a formula
    with zipfile.ZipFile(report.file_path.path) as archive:
        sheets = [name for name in archive.namelist() if name.startswith("xl/worksheets/")]
        assert all("<f>" not in archive.read(name).decode() for name in sheets)  # no formula anywhere


def test_failed_render_leaves_no_file_and_the_task_fails(director, queued, fake_pdf_renderer) -> None:
    report = request(director)
    fake_pdf_renderer.fail = OSError("Pango is missing")
    with pytest.raises(OSError):
        tasks.run_build_report(report.pk)
    report.refresh_from_db()
    assert report.file_path.name == ""
    assert not (Path(settings.MEDIA_ROOT) / "reports").exists() or not any((Path(settings.MEDIA_ROOT) / "reports").rglob("*.pdf"))


def test_state_is_failed_when_the_task_failed(director, queued) -> None:
    report = request(director)
    now = timezone.now()
    Task.objects.create(id="t1", name=services.task_name(report.pk), func="reports.tasks.run_build_report",
                        started=now, stopped=now, success=False)
    assert services.report_state(report) == services.FAILED
    assert services.failed_report_ids([report.pk]) == {report.pk}


# --- AI narrative ------------------------------------------------------------------------------------


def test_ai_summary_is_included_labeled_and_its_prompt_saved(director, queued, year_data, fake_gemini, fake_pdf_renderer) -> None:
    fake_gemini.replies.append(fake_gemini.say("In 2026 the TBI conducted one workshop with 2 registrants."))
    report = request(director, included_sections=["summary", "ai_summary", "activities", "by_category", "evaluation", "startups"])
    services.build_report(report.pk, now=TEST_NOW)
    report.refresh_from_db()

    text = fake_pdf_renderer.last_text()
    assert AI_LABEL in text and "In 2026 the TBI conducted one workshop" in text
    (call,) = fake_gemini.generate_calls
    assert call["tools"] == []
    prompt = call["turns"][0].text
    assert report.ai_prompt == f"[System instruction]\n{call['system_instruction']}\n\n[Prompt]\n{prompt}"
    # Only figures, activity titles and dates: never names, emails, comments, answers or startup names.
    assert "Pitching Workshop" in prompt and "2026-03-10" in prompt
    for private in ("Ana Dela Cruz", "Ben Santos", "ana.private@example.com", "ben.private@example.com",
                    "private comment", "Call me at 0917", "Secret Startup Inc", "founder@secret.test"):
        assert private not in report.ai_prompt
    # The tables still come from the database.
    assert "Secret Startup Inc" in text


def test_report_is_still_generated_when_gemini_fails(director, queued, year_data, fake_gemini, fake_pdf_renderer) -> None:
    def busy(**kwargs):
        raise AssistantUnavailable("The AI service is busy (rate limit reached). Please try again in a minute.", "rate_limit")

    fake_gemini.replies.append(busy)
    report = request(director, included_sections=["summary", "ai_summary"])
    assert services.build_report(report.pk, now=TEST_NOW) == "generated"
    report.refresh_from_db()
    assert services.report_state(report) == services.READY
    assert "AI summary not included: The AI service is busy" in fake_pdf_renderer.last_text()
    assert report.ai_prompt.startswith("[System instruction]")  # what was attempted is recorded


def test_report_is_still_generated_when_gemini_is_not_set_up(director, queued, year_data, monkeypatch, settings) -> None:
    from assistant import gemini

    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = ""
    report = request(director, format="excel", included_sections=["summary", "ai_summary"])
    services.build_report(report.pk, now=TEST_NOW)
    report.refresh_from_db()
    assert report.ai_prompt is None  # nothing was sent
    sheet = load_workbook(report.file_path.path)["AI summary"]
    assert sheet["A7"].value.startswith("AI summary not included: The AI assistant is not set up")


def test_unexpected_ai_error_never_stops_the_report(director, queued, year_data, fake_gemini, fake_pdf_renderer) -> None:
    def broken(**kwargs):
        raise RuntimeError("boom")

    fake_gemini.replies.append(broken)
    report = request(director, included_sections=["ai_summary"])
    assert services.build_report(report.pk, now=TEST_NOW) == "generated"
    assert "AI summary not included: The AI service failed unexpectedly." in fake_pdf_renderer.last_text()


# --- Downloads ----------------------------------------------------------------------------------------


@pytest.fixture
def ready_report(director, queued, year_data) -> GeneratedReport:
    report = request(director, format="excel")
    services.build_report(report.pk, now=TEST_NOW)
    report.refresh_from_db()
    return report


def test_any_active_staff_member_downloads_as_an_attachment(ready_report, view_only, login) -> None:
    response = login(view_only).get(reverse("reports:download", args=[ready_report.pk]))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert f"annual-accomplishment-report-2026-{ready_report.pk}.xlsx" in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Type"].startswith("application/vnd.openxmlformats")
    load_workbook(BytesIO(b"".join(response.streaming_content)))


def test_download_refuses_anonymous_and_startups(ready_report) -> None:
    url = reverse("reports:download", args=[ready_report.pk])
    assert Client().get(url).status_code == 302
    startup = Startup.objects.get(email="founder@secret.test")
    client = Client()
    session = client.session
    session[STARTUP_SESSION_KEY] = startup.pk
    session.save()
    response = client.get(url)
    assert response.status_code == 302 and "login" in response["Location"]


def test_deactivated_member_cannot_download(ready_report, view_only, login) -> None:
    client = login(view_only)
    view_only.is_active = False
    view_only.save()
    assert client.get(reverse("reports:download", args=[ready_report.pk])).status_code == 302


def test_pending_report_is_404_and_files_are_not_under_media_urls(director, queued, login) -> None:
    report = request(director)
    assert login(director).get(reverse("reports:download", args=[report.pk])).status_code == 404
    assert Client().get("/media/reports/").status_code == 404


def test_list_shows_states(ready_report, director, queued, login) -> None:
    pending = request(director)
    states = {r["report_id"]: (r["state"], r["download_url"]) for r in login(director).get(reverse("reports:api_reports")).json()["reports"]}
    assert states[ready_report.pk] == ("ready", reverse("reports:download", args=[ready_report.pk]))
    assert states[pending.pk] == ("pending", None)


# --- Command and admin ----------------------------------------------------------------------------


def test_generate_report_command_builds_now(director, year_data, queued) -> None:
    out = StringIO()
    call_command("generate_report", "--type", "participation", "--format", "excel", "--member", director.email,
                 "--start", "2026-01-01", "--end", "2026-12-31", stdout=out)
    report = GeneratedReport.objects.get()
    assert queued == []  # built right away, not queued
    assert services.report_state(report) == services.READY
    assert report.file_path.path in out.getvalue()


def test_generate_report_command_builds_a_pending_report(director, queued) -> None:
    report = request(director)
    call_command("generate_report", "--report", str(report.pk), stdout=StringIO())
    report.refresh_from_db()
    assert report.file_path


def test_reports_are_view_only_in_the_admin(ready_report, admin_member) -> None:
    client = Client()
    client.force_login(admin_member)
    assert client.get(reverse("admin:reports_generatedreport_changelist")).status_code == 200
    from django.contrib import admin

    model_admin = admin.site._registry[GeneratedReport]
    assert not model_admin.has_add_permission(None)
    assert not model_admin.has_delete_permission(None, ready_report)
