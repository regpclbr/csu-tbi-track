"""Date range rules for generated reports and the dashboard (ASSUMPTIONS item 213)."""

import json
from datetime import date
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.urls import reverse
from openpyxl import load_workbook

from reports import metrics, services
from reports.content import NO_ACTIVITIES, range_in_words
from reports.models import GeneratedReport

from .conftest import TEST_NOW

pytestmark = pytest.mark.django_db

API = reverse("reports:api_reports")


@pytest.fixture
def queued(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(services, "queue_build", calls.append)
    return calls


def post(client, **data):
    payload = {"report_type": "accomplishment", "format": "pdf", "date_range_start": "2026-01-01", "date_range_end": "2026-06-30"}
    payload.update(data)
    payload = {key: value for key, value in payload.items() if value is not None}
    return client.post(API, data=json.dumps(payload), content_type="application/json")


def request(member, start: str, end: str, **data) -> GeneratedReport:
    payload = {"report_type": "accomplishment", "format": "pdf", "date_range_start": start, "date_range_end": end, **data}
    return services.request_report(member, payload, now=TEST_NOW)


# --- Required, format, order --------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["date_range_start", "date_range_end"])
def test_reports_need_both_dates(director, login, queued, missing) -> None:
    response = post(login(director), **{missing: None})
    assert response.status_code == 400
    assert response.json()["errors"][missing] == ["Required. Use the format YYYY-MM-DD, for example 2026-06-30."]
    assert not GeneratedReport.objects.exists()


def test_empty_strings_count_as_missing(director, queued) -> None:
    with pytest.raises(ValidationError) as error:
        request(director, "", "")
    assert set(error.value.message_dict) == {"date_range_start", "date_range_end"}


@pytest.mark.parametrize("value", [
    "2026/01/01", "01-05-2026", "2026-1-5", "20260105",  # not YYYY-MM-DD
    "2026-W02-1",  # an ISO week date: Python would read it as Jan 5, 2026
    "2026-01-05T00:00", "Jan 5, 2026", 20260105, ["2026-01-05"],
])
def test_dates_must_be_written_yyyy_mm_dd(director, login, queued, value) -> None:
    response = post(login(director), date_range_start=value)
    assert response.status_code == 400
    assert response.json()["errors"]["date_range_start"] == ["Use the format YYYY-MM-DD, for example 2026-06-30."]


def test_a_date_that_does_not_exist_is_refused(director, login, queued) -> None:
    response = post(login(director), date_range_end="2026-02-30")
    assert response.status_code == 400
    assert response.json()["errors"]["date_range_end"][0].startswith("2026-02-30 is not a real date.")


def test_end_before_start_is_a_clear_400(director, login, queued) -> None:
    response = post(login(director), date_range_start="2026-06-30", date_range_end="2026-06-01")
    assert response.status_code == 400
    assert response.json()["errors"]["date_range_end"] == [
        "The end date (2026-06-01) is before the start date (2026-06-30). The end date must be on or after the start date."
    ]


def test_a_one_day_range_is_allowed(director, queued) -> None:
    report = request(director, "2026-03-05", "2026-03-05")
    assert report.date_range_start == report.date_range_end == date(2026, 3, 5)


def test_the_command_needs_both_dates(director, queued) -> None:
    with pytest.raises(CommandError, match="date_range_start: Required"):
        call_command("generate_report", "--type", "accomplishment", "--end", "2026-06-30", "--member", director.email,
                     stdout=StringIO())


def test_the_dashboard_still_defaults_missing_dates_but_checks_the_format(view_only, login) -> None:
    client = login(view_only)
    data = client.get(reverse("reports:api_dashboard")).json()
    assert data["filters"]["start"].endswith("-01-01") and data["filters"]["end"].endswith("-12-31")
    response = client.get(reverse("reports:api_dashboard"), {"start": "20260101"})
    assert response.status_code == 400 and "start" in response.json()["errors"]


# --- The range in words ---------------------------------------------------------------------------


@pytest.mark.parametrize("start, end, words", [
    (date(2026, 1, 1), date(2026, 6, 30), "January 1 – June 30, 2026"),
    (date(2026, 1, 1), date(2026, 12, 31), "January 1 – December 31, 2026"),
    (date(2026, 3, 1), date(2026, 3, 31), "March 1 – 31, 2026"),
    (date(2025, 12, 1), date(2026, 1, 31), "December 1, 2025 – January 31, 2026"),
    (date(2026, 3, 5), date(2026, 3, 5), "March 5, 2026"),
])
def test_range_in_words(start, end, words) -> None:
    assert range_in_words(start, end) == words


def test_pdf_header_shows_the_exact_range_in_words(director, queued, make_activity, fake_pdf_renderer) -> None:
    make_activity(start=date(2026, 3, 10))
    services.build_report(request(director, "2026-01-01", "2026-06-30").pk, now=TEST_NOW)
    text = fake_pdf_renderer.last_text()
    assert "Accomplishment Report: January 1 – June 30, 2026" in text
    assert "Period: January 1 – June 30, 2026 (2026-01-01 to 2026-06-30)" in text


def test_excel_report_info_sheet_shows_the_exact_range(director, queued, make_activity) -> None:
    make_activity(start=date(2026, 3, 10))
    report = request(director, "2026-01-01", "2026-06-30", format="excel")
    services.build_report(report.pk, now=TEST_NOW)
    report.refresh_from_db()
    workbook = load_workbook(report.file_path.path)
    assert workbook.sheetnames[0] == "Report info"
    rows = {row[0].value: row[1].value for row in workbook["Report info"].iter_rows(min_row=7)}
    assert rows["Period"] == "January 1 – June 30, 2026"
    assert rows["Period start"].date() == date(2026, 1, 1) and rows["Period end"].date() == date(2026, 6, 30)
    assert "Result" not in rows  # there were conducted activities


def test_api_lists_the_range_used(director, view_only, login, queued) -> None:
    created = post(login(director), date_range_start="2026-01-01", date_range_end="2026-06-30").json()["report"]
    assert (created["date_range_start"], created["date_range_end"]) == ("2026-01-01", "2026-06-30")
    assert created["date_range_text"] == "January 1 – June 30, 2026"
    listed = login(view_only).get(API).json()["reports"][0]
    assert listed["date_range_text"] == "January 1 – June 30, 2026"
    dashboard = login(view_only).get(reverse("reports:api_dashboard"), {"start": "2026-01-01", "end": "2026-06-30"}).json()
    assert dashboard["filters"]["range_text"] == "January 1 – June 30, 2026"


# --- A range with no conducted activities (e.g. entirely in the future) ---------------------------


def test_future_range_is_allowed_and_says_no_conducted_activities(director, queued, make_activity, fake_gemini, fake_pdf_renderer) -> None:
    make_activity("Last year", start=date(2025, 6, 1))
    report = request(director, "2027-01-01", "2027-06-30",
                     included_sections=["summary", "ai_summary", "activities", "by_category", "monthly", "evaluation"])
    assert services.build_report(report.pk, now=TEST_NOW) == "generated"
    report.refresh_from_db()

    html, text = fake_pdf_renderer.last, fake_pdf_renderer.last_text()
    assert "Period: January 1 – June 30, 2027" in text
    assert text.count(NO_ACTIVITIES) >= 5  # the header notice and each period section
    assert "<table" not in html and "<svg" not in html  # no empty tables, no empty charts
    assert "AI summary not included: no conducted activities in this period." in text
    assert fake_gemini.generate_calls == [] and report.ai_prompt is None  # nothing sent to Gemini


def test_future_range_in_excel(director, queued) -> None:
    report = request(director, "2027-01-01", "2027-12-31", format="excel", included_sections=["summary", "activities"])
    services.build_report(report.pk, now=TEST_NOW)
    report.refresh_from_db()
    workbook = load_workbook(report.file_path.path)
    rows = {row[0].value: row[1].value for row in workbook["Report info"].iter_rows(min_row=7)}
    assert rows["Result"] == NO_ACTIVITIES
    for name in ("Summary", "Activities"):
        sheet = workbook[name]
        assert sheet["A7"].value == NO_ACTIVITIES and sheet.max_row == 7 and not sheet.tables, name


def test_certificates_section_keeps_its_own_dates(director, queued, fake_pdf_renderer) -> None:
    """Certificates count by when they were issued, so that section still shows its tables."""
    report = request(director, "2027-01-01", "2027-03-31", included_sections=["summary", "certificates"])
    services.build_report(report.pk, now=TEST_NOW)
    text = fake_pdf_renderer.last_text()
    assert NO_ACTIVITIES in text and "Issued and emailed in the period" in text


def test_a_past_range_without_activities_says_so_too(director, queued, make_activity, fake_pdf_renderer) -> None:
    make_activity(start=date(2026, 3, 10), status="Ongoing")  # not conducted: never counted
    services.build_report(request(director, "2026-01-01", "2026-06-30").pk, now=TEST_NOW)
    assert NO_ACTIVITIES in fake_pdf_renderer.last_text()


def test_max_range_still_applies(director, queued) -> None:
    with pytest.raises(ValidationError) as error:
        request(director, "2010-01-01", "2026-12-31")
    assert error.value.message_dict["date_range_end"] == ["The date range can be at most 10 years."]
    assert metrics.MAX_RANGE_DAYS == 3660
