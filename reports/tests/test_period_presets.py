"""Quarterly, semi-annual and annual reports (ASSUMPTIONS 399): the period presets (dates only from
reports.services.preset_range), the Year choice, the title and file name named after the period, and "What report do
you need?" filling the presets. Gemini is always the fake."""

from datetime import date, datetime

import pytest
from django.http import QueryDict
from django.urls import reverse

from assistant.testing import FakeGemini
from reports import pages, request_reader, services
from reports.content import period_name, report_title
from reports.models import GeneratedReport

from .conftest import TEST_NOW
from .test_form_fill import Fields

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
FORM = {"report_type": "accomplishment", "included_sections": ["summary", "activities"], "range": "this_month",
        "date_range_start": "", "date_range_end": "", "format": "pdf", "activity_type": ""}


@pytest.fixture
def queued(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(services, "queue_build", calls.append)
    return calls


# --- The presets ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key, year, start, end", [
    ("q1", 2026, date(2026, 1, 1), date(2026, 3, 31)),
    ("q2", 2026, date(2026, 4, 1), date(2026, 6, 30)),
    ("q3", 2026, date(2026, 7, 1), date(2026, 9, 30)),
    ("q4", 2026, date(2026, 10, 1), date(2026, 12, 31)),
    ("h1", 2026, date(2026, 1, 1), date(2026, 6, 30)),
    ("h2", 2026, date(2026, 7, 1), date(2026, 12, 31)),
    ("year", 2026, date(2026, 1, 1), date(2026, 12, 31)),
    ("q1", 2028, date(2028, 1, 1), date(2028, 3, 31)),  # a leap year changes nothing at the quarter's end
    ("year", 2025, date(2025, 1, 1), date(2025, 12, 31)),
])
def test_preset_range(key, year, start, end) -> None:
    assert services.preset_range(key, year) == (start, end)
    assert services.matching_preset(start, end) == (key, year)


def test_unknown_presets_and_years_give_nothing() -> None:
    assert services.preset_range("q5", 2026) is None
    assert services.preset_range("this_month", 2026) is None  # not a preset of a year
    assert services.preset_range("q1", 1999) is None and services.preset_range("q1", 2101) is None
    assert services.matching_preset(date(2026, 3, 5), date(2026, 4, 2)) is None


def test_year_choices() -> None:
    today = date(2026, 11, 17)
    assert pages.year_choices(today) == [2027, 2026, 2025, 2024, 2023, 2022, 2021]
    assert pages.year_choices(today, also=2018)[-1] == 2018  # a filled year outside the list is offered too
    assert pages.year_choices(today, also=1990) == pages.year_choices(today)


def test_the_form_starts_on_this_quarter_of_this_year() -> None:
    form = pages.empty_form(date(2026, 11, 17))
    assert (form.range_key, form.period_year, form.start, form.end) == ("q4", 2026, "2026-10-01", "2026-12-31")
    form = pages.empty_form(date(2026, 2, 3))
    assert (form.range_key, form.start, form.end) == ("q1", "2026-01-01", "2026-03-31")


def test_posted_period_and_year_without_javascript() -> None:
    today = date(2026, 11, 17)
    form = pages.form_from_post(QueryDict("range=q3&period_year=2025&date_range_start=2020-01-01&date_range_end=x"), today)
    assert (form.start, form.end, form.range_key, form.period_year) == ("2025-07-01", "2025-09-30", "q3", 2025)
    form = pages.form_from_post(QueryDict("range=h2&period_year=abc"), today)  # a bad year: this year
    assert (form.start, form.end) == ("2026-07-01", "2026-12-31")
    form = pages.form_from_post(QueryDict("range=year&period_year=1800"), today)
    assert (form.start, form.end) == ("2026-01-01", "2026-12-31")
    form = pages.form_from_post(QueryDict("range=this_month&period_year=2020"), today)  # the year is ignored
    assert (form.start, form.end, form.range_key) == ("2026-11-01", "2026-11-30", "this_month")
    form = pages.form_from_post(QueryDict("range=custom&period_year=2020&date_range_start=2026-03-05&date_range_end=2026-04-02"), today)
    assert (form.start, form.end, form.range_key) == ("2026-03-05", "2026-04-02", "custom")


def test_matching_range_key_prefers_the_named_preset() -> None:
    today = date(2026, 11, 17)
    assert pages.matching_range_key(date(2026, 10, 1), date(2026, 12, 31), today) == ("q4", 2026)
    assert pages.matching_range_key(date(2024, 1, 1), date(2024, 6, 30), today) == ("h1", 2024)
    assert pages.matching_range_key(date(2026, 11, 1), date(2026, 11, 30), today) == ("this_month", 2026)
    assert pages.matching_range_key(date(2026, 3, 5), date(2026, 4, 2), today) == ("custom", 2026)


def test_generate_with_q3_2025_saves_those_dates(director, login, queued) -> None:
    login(director).post(reverse("reports:list"), {**FORM, "range": "q3", "period_year": "2025"})
    report = GeneratedReport.objects.get()
    assert (report.date_range_start, report.date_range_end) == (date(2025, 7, 1), date(2025, 9, 30))


def test_the_page_offers_every_preset_with_its_dates_from_the_server(director, login) -> None:
    html = login(director).get(reverse("reports:list")).content.decode()
    for label in services.PERIOD_PRESETS.values():
        assert label in html
    assert 'id="report-preset-dates"' in html and '"q3": {"start": "2026-07-01", "end": "2026-09-30"' in html
    assert 'id="report-year"' in html and 'name="period_year"' in html


# --- Title and file name ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("start, end, title", [
    (date(2026, 7, 1), date(2026, 9, 30), "Accomplishment Report - Q3 2026"),
    (date(2026, 1, 1), date(2026, 6, 30), "Semi-annual Accomplishment Report - January to June 2026"),
    (date(2026, 7, 1), date(2026, 12, 31), "Semi-annual Accomplishment Report - July to December 2026"),
    (date(2026, 1, 1), date(2026, 12, 31), "Annual Accomplishment Report - 2026"),
    (date(2026, 3, 5), date(2026, 4, 2), "Accomplishment Report - March 5 – April 2, 2026"),
    (date(2026, 7, 1), date(2026, 9, 29), "Accomplishment Report - July 1 – September 29, 2026"),  # not a whole quarter
])
def test_report_title(start, end, title) -> None:
    assert report_title("Accomplishment Report", start, end) == title


def test_period_names() -> None:
    assert period_name(date(2026, 7, 1), date(2026, 9, 30)).slug == "q3-2026"
    assert period_name(date(2026, 1, 1), date(2026, 6, 30)).slug == "jan-jun-2026"
    assert period_name(date(2026, 7, 1), date(2026, 12, 31)).slug == "jul-dec-2026"
    assert period_name(date(2026, 1, 1), date(2026, 12, 31)).label == "2026"
    assert period_name(date(2026, 1, 1), date(2027, 12, 31)) is None


@pytest.mark.parametrize("report_type, start, end, fmt, name", [
    ("accomplishment", "2026-07-01", "2026-09-30", "pdf", "accomplishment-report-q3-2026-{pk}.pdf"),
    ("participation", "2026-01-01", "2026-06-30", "pdf", "semi-annual-participation-report-jan-jun-2026-{pk}.pdf"),
    ("accomplishment", "2026-01-01", "2026-12-31", "excel", "annual-accomplishment-report-2026-{pk}.xlsx"),
    ("document_compliance", "2026-10-01", "2026-12-31", "pdf", "document-compliance-report-q4-2026-{pk}.pdf"),
    ("accomplishment", "2026-03-05", "2026-04-02", "pdf", "accomplishment-report-20260305-20260402-{pk}.pdf"),  # unchanged
])
def test_download_name(director, queued, report_type, start, end, fmt, name) -> None:
    report = services.request_report(director, {"report_type": report_type, "format": fmt, "date_range_start": start,
                                                "date_range_end": end}, now=TEST_NOW)
    assert services.download_name(report) == name.format(pk=report.pk)


def test_the_pdf_and_the_history_name_the_quarter(director, login, queued, fake_pdf_renderer) -> None:
    report = services.request_report(director, {"report_type": "participation", "format": "pdf",
                                                "date_range_start": "2026-07-01", "date_range_end": "2026-09-30"}, now=TEST_NOW)
    services.build_report(report.pk, now=TEST_NOW)
    text = fake_pdf_renderer.last_text()
    assert "Participation Report - Q3 2026" in text
    assert "Period: Q3 2026 (July 1 – September 30, 2026) (2026-07-01 to 2026-09-30)" in text
    html = login(director).get(reverse("reports:list")).content.decode()
    assert "Q3 2026" in html and "July 1 – September 30, 2026" in html


# --- "What report do you need?" fills the presets ----------------------------------------------------------------


def tool(**args):
    return FakeGemini.call(request_reader.TOOL_NAME, **args)


def filled(login, director, fake_gemini, clock, text: str, **args) -> str:
    clock(datetime(2026, 11, 17, 9, 0))
    fake_gemini.replies.append(tool(**args))
    return login(director).post(reverse("reports:fill"), {**FORM, "request_text": text}, **HTMX).content.decode()


def test_quarterly_report_for_q3_selects_q3_of_this_year(director, login, fake_gemini, clock) -> None:
    html = filled(login, director, fake_gemini, clock, "quarterly participation report for Q3 in Excel",
                  report_type="participation", period_words="Q3", format="excel")
    fields = Fields(html)
    assert fields.checked["range"] == ["q3"] and fields.selects["report-year"] == "2026"
    assert fields.selects["summary-range"] == "q3" and fields.selects["summary-year"] == "2026"
    assert (fields.values["date_range_start"], fields.values["date_range_end"]) == ("2026-07-01", "2026-09-30")
    assert 'x-text="rangeText()">July 1 – September 30, 2026</p>' in html


def test_a_year_elsewhere_in_the_request_is_used(director, fake_gemini) -> None:
    fake_gemini.replies.append(tool(period_words="Q3"))  # Gemini copied only "Q3"
    result = services.fill_report_form(director, "Q3 accomplishment report for 2025", now=TEST_NOW)
    assert (result.start, result.end) == (date(2025, 7, 1), date(2025, 9, 30))


def test_annual_report_2026_selects_the_whole_year(director, fake_gemini) -> None:
    fake_gemini.replies.append(tool(report_type="accomplishment", period_words="annual report 2026"))
    result = services.fill_report_form(director, "annual report 2026", now=TEST_NOW)
    assert (result.start, result.end) == (date(2026, 1, 1), date(2026, 12, 31))
    assert pages.matching_range_key(result.start, result.end, date(2026, 11, 17)) == ("year", 2026)


def test_annual_report_without_period_words_still_works(director, fake_gemini) -> None:
    fake_gemini.replies.append(tool(report_type="accomplishment"))  # Gemini returned no period words
    result = services.fill_report_form(director, "annual accomplishment report", now=TEST_NOW)
    assert (result.start, result.end) == (date(2026, 1, 1), date(2026, 12, 31))


@pytest.mark.parametrize("text, words, note", [
    ("quarterly participation report", "quarterly", services.FILL_WHICH["quarterly"]),
    ("semi-annual accomplishment report", "semi-annual", services.FILL_WHICH["semi-annual"]),
])
def test_quarterly_without_saying_which_asks_and_keeps_the_dates(director, fake_gemini, text, words, note) -> None:
    fake_gemini.replies.append(tool(period_words=words))
    result = services.fill_report_form(director, text, now=TEST_NOW)
    assert result.start is None and result.end is None
    assert note in result.notes
    assert not any(n.startswith("Not in your request") and "period" in n for n in result.notes)


def test_gemini_saying_quarterly_does_not_hide_the_quarter_in_the_request(director, fake_gemini) -> None:
    fake_gemini.replies.append(tool(period_words="quarterly"))
    result = services.fill_report_form(director, "quarterly report, second quarter", now=TEST_NOW)
    assert (result.start, result.end) == (date(2026, 4, 1), date(2026, 6, 30))
