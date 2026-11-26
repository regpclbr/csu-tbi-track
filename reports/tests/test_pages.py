"""Phase F5: the Reports page (/reports/): the "New report" form (can_create_activity only), the service's errors under
their fields, the history with its states, and the polling of pending rows that stops when they are ready or failed."""

import json
import re
from datetime import date, datetime

import pytest
from django.urls import reverse
from django.utils import timezone
from django_q.models import Task

from reports import pages, services
from reports.models import GeneratedReport

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
VALID = {"report_type": "participation", "included_sections": ["summary", "monthly"], "range": "custom",
         "date_range_start": "2026-01-01", "date_range_end": "2026-06-30", "format": "excel"}


@pytest.fixture
def queued(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(services, "queue_build", calls.append)
    return calls


def make_report(member, file_path: str = "", **changes) -> GeneratedReport:
    values = {"report_type": "accomplishment", "included_sections": json.dumps(["summary", "ai_summary"]),
              "date_range_start": date(2026, 1, 1), "date_range_end": date(2026, 6, 30), "format": "pdf"}
    values.update(changes)
    return GeneratedReport.objects.create(generated_by=member, file_path=file_path, **values)


def fail(report: GeneratedReport) -> None:
    now = timezone.now()
    Task.objects.create(id=f"t{report.pk}", name=services.task_name(report.pk), func="reports.tasks.run_build_report",
                        started=now, stopped=now, success=False)


# --- The page and the form ---------------------------------------------------------------------------------------


def test_page_shows_the_form_to_members_who_can_generate(director, login) -> None:
    html = login(director).get(reverse("reports:list")).content.decode()
    assert "is coming soon" not in html and 'id="new-report"' in html
    assert html.index("Accomplishment Report") < html.index("Participation Report") < html.index("Evaluation Report")
    for label in ("Document Compliance Report", "Certificates", "Include AI summary", "This month", "Q1 (Jan–Mar)",
                  "Q4 (Oct–Dec)", "First half (Jan–Jun)", "Second half (Jul–Dec)", "Whole year (Jan–Dec)", "Custom dates",
                  'name="period_year"', "Excel", "All activity types"):
        assert label in html
    assert 'value="ai_summary"' not in html  # the switch adds it, it is not a pill
    assert "No reports yet" in html


def test_without_can_create_activity_there_is_no_form_and_posting_is_refused(view_only, login, queued) -> None:
    client = login(view_only)
    html = client.get(reverse("reports:list")).content.decode()
    assert 'id="new-report"' not in html and "Generating a new one needs a role" in html
    assert client.post(reverse("reports:list"), VALID).status_code == 403
    assert not GeneratedReport.objects.exists() and queued == []


def test_posting_requests_the_report_through_the_service(director, login, queued, django_capture_on_commit_callbacks) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        response = login(director).post(reverse("reports:list"), {**VALID, "ai_summary": "on"}, follow=True)
    report = GeneratedReport.objects.get()
    assert services.sections_of(report) == ["summary", "ai_summary", "monthly"]
    assert (report.report_type, report.format, report.date_range_end) == ("participation", "excel", date(2026, 6, 30))
    assert queued == [report.pk]
    assert f"Report {report.pk} was requested" in [str(m) for m in response.context["messages"]][0]


def test_a_quick_choice_sets_the_dates_without_javascript(director, login, queued, clock) -> None:
    clock(datetime(2026, 10, 28, 10, 0))
    login(director).post(reverse("reports:list"), {**VALID, "range": "h1", "period_year": "2025",
                                                   "date_range_start": "", "date_range_end": ""})
    report = GeneratedReport.objects.get()
    assert (report.date_range_start, report.date_range_end) == (date(2025, 1, 1), date(2025, 6, 30))


def test_service_errors_appear_under_their_fields(director, login, queued) -> None:
    response = login(director).post(reverse("reports:list"), {
        **VALID, "report_type": "", "format": "", "date_range_start": "2026-06-30", "date_range_end": "2026-06-01",
    })
    assert response.status_code == 400 and not GeneratedReport.objects.exists()
    html = response.content.decode()
    assert re.search(r'<details id="report-more"[^>]*\sopen>', html)  # "More settings" opens to show the errors
    assert re.search(r'id="report-end-error" class="field-error">The end date \(2026-06-01\) is before the start date', html)
    assert 'id="report-type-error"' in html and 'id="report-format-error"' in html
    assert 'value="2026-06-30"' in html  # the typed values are kept


def test_no_pills_means_the_type_defaults(director, login, queued) -> None:
    data = {key: value for key, value in VALID.items() if key != "included_sections"}
    response = login(director).post(reverse("reports:list"), data)  # no pills and no AI: the service uses the defaults
    assert response.status_code == 302
    assert services.sections_of(GeneratedReport.objects.get()) == ["summary", "by_category", "category_by_activity", "monthly"]


# --- History and polling -----------------------------------------------------------------------------------------


def test_history_shows_type_sections_range_format_requester_and_states(director, view_only, login) -> None:
    pending = make_report(director)
    ready = make_report(director, file_path="reports/2026/report-x.pdf", report_type="participation",
                        included_sections=json.dumps(["summary"]), format="excel")
    failed = make_report(director, report_type="evaluation")
    fail(failed)
    html = login(view_only).get(reverse("reports:list")).content.decode()  # everyone sees the history
    assert "Accomplishment Report" in html and "Summary, AI-generated summary" in html
    assert "January 1 – June 30, 2026" in html and "Test Director" in html and "Excel" in html
    for label in ("Pending", "Ready", "Failed"):
        assert f'badge-{dict(Pending="info", Ready="success", Failed="danger")[label]}">{label}<' in html
    assert reverse("reports:download", args=[ready.pk]) in html
    assert reverse("reports:download", args=[pending.pk]) not in html
    assert "qcluster" in html
    assert html.count('hx-trigger="every 4s"') == 1  # only the pending row polls
    assert f'hx-get="{reverse("reports:row", args=[pending.pk])}?polls=1"' in html


def test_a_pending_row_keeps_polling_and_counts_its_polls(director, login) -> None:
    report = make_report(director)
    response = login(director).get(reverse("reports:row", args=[report.pk]) + "?polls=7", **HTMX)
    html = response.content.decode()
    assert html.lstrip().startswith("<tr") and 'hx-trigger="every 4s"' in html and "?polls=8" in html
    assert "HX-Trigger" not in response  # nothing to announce yet


def test_polling_stops_when_the_report_is_ready_and_announces_it(director, login) -> None:
    report = make_report(director)
    report.file_path = "reports/2026/report-1.pdf"
    report.save()
    response = login(director).get(reverse("reports:row", args=[report.pk]) + "?polls=3", **HTMX)
    html = response.content.decode()
    assert "hx-trigger" not in html and "Ready" in html and reverse("reports:download", args=[report.pk]) in html
    assert json.loads(response["HX-Trigger"])["report-state"].endswith("is ready to download.")


def test_polling_stops_when_the_report_failed(director, login) -> None:
    report = make_report(director)
    fail(report)
    response = login(director).get(reverse("reports:row", args=[report.pk]), **HTMX)
    assert "hx-trigger" not in response.content.decode() and "Failed" in response.content.decode()
    assert json.loads(response["HX-Trigger"])["report-state"].endswith("failed.")


def test_polling_gives_up_after_fifteen_minutes(director, login) -> None:
    report = make_report(director)
    html = login(director).get(reverse("reports:row", args=[report.pk]) + f"?polls={pages.MAX_POLLS}", **HTMX).content.decode()
    assert "hx-trigger" not in html and "Refresh the page to check again" in html


def test_row_and_page_need_a_staff_login(director, client) -> None:
    report = make_report(director)
    assert client.get(reverse("reports:row", args=[report.pk])).status_code == 302
    assert client.get(reverse("reports:list")).status_code == 302


def test_the_pager_swaps_only_the_history(director, login) -> None:
    for _ in range(services.DEFAULT_PAGE_SIZE + 1):
        make_report(director)
    html = login(director).get(reverse("reports:list") + "?page=2", **HTMX).content.decode()
    assert html.lstrip().startswith('<div id="report-history"') and 'id="new-report"' not in html
    assert "Page 2 of 2" in html


# --- Display helpers ---------------------------------------------------------------------------------------------


def test_date_presets() -> None:
    today = date(2026, 11, 5)
    presets = pages.date_presets(today, pages.year_choices(today))
    keys = [choice["key"] for choice in presets["choices"]]
    assert keys == ["this_month", "q1", "q2", "q3", "q4", "h1", "h2", "year", "custom"]
    assert [c["key"] for c in presets["choices"] if c["yearly"]] == ["q1", "q2", "q3", "q4", "h1", "h2", "year"]
    assert list(presets["dates"]) == ["2027", "2026", "2025", "2024", "2023", "2022", "2021"]
    dates = presets["dates"]
    assert (dates["2026"]["this_month"]["start"], dates["2026"]["this_month"]["end"]) == ("2026-11-01", "2026-11-30")
    assert (dates["2026"]["q4"]["start"], dates["2026"]["q4"]["end"]) == ("2026-10-01", "2026-12-31")
    assert dates["2025"]["h1"]["text"] == "January 1 – June 30, 2025"
    assert (dates["2024"]["q1"]["start"], dates["2024"]["q1"]["end"]) == ("2024-01-01", "2024-03-31")
    assert (dates["2025"]["year"]["start"], dates["2025"]["year"]["end"]) == ("2025-01-01", "2025-12-31")
    assert "custom" not in dates["2026"]


def test_form_from_post_keeps_typed_dates_only_for_custom() -> None:
    from django.http import QueryDict

    post = QueryDict("range=year&period_year=2026&date_range_start=2020-01-01&date_range_end=2020-02-01&included_sections=ai_summary")
    form = pages.form_from_post(post, date(2026, 3, 1))
    assert (form.start, form.end, form.range_key, form.period_year) == ("2026-01-01", "2026-12-31", "year", 2026)
    assert form.sections == [] and form.ai_summary is False  # only the switch adds the AI summary
    form = pages.form_from_post(QueryDict("range=custom&date_range_start=2020-01-01&date_range_end=x"), date(2026, 3, 1))
    assert (form.start, form.end) == ("2020-01-01", "x")  # the service checks them
