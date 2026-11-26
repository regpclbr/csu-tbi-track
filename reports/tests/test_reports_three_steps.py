"""The Reports page in three steps (ASSUMPTIONS 467): one list of kinds with a description each, step 2 only what the
kind needs with defaults, step 3 notes and files, one Generate button, Options closed; at most three clicks with the
defaults (Reports, the kind, Generate). The rules are unchanged (reports.services, reports.generator,
registrations.attendance_forms)."""

from datetime import date, datetime
from html.parser import HTMLParser

import pytest
from django.urls import NoReverseMatch, reverse

from reports import hub
from reports.models import GeneratedReport, ReportDraft

pytestmark = pytest.mark.django_db


class FormValues(HTMLParser):
    """What a browser would post for one <form>: checked radios / checkboxes, selected options, inputs, textareas."""

    def __init__(self, html: str, form_id: str | None = None, action: str | None = None) -> None:
        super().__init__()
        self.form_id, self.action = form_id, action
        self.inside = False
        self.data: dict[str, list[str]] = {}
        self.select: str | None = None
        self.first_option: str | None = None
        self.selected = False
        self.textarea: str | None = None
        self.feed(html)

    def _matches(self, attrs: dict) -> bool:
        return (self.form_id and attrs.get("id") == self.form_id) or (self.action and attrs.get("action") == self.action)

    def handle_starttag(self, tag, attrs) -> None:
        attrs = dict(attrs)
        if tag == "form" and self._matches(attrs):
            self.inside = True
        if not self.inside:
            return
        name = attrs.get("name")
        if tag == "input" and name and attrs.get("type") != "file":
            if attrs.get("type") in ("radio", "checkbox"):
                if "checked" in attrs:
                    self.data.setdefault(name, []).append(attrs.get("value", "on"))
            else:
                self.data.setdefault(name, []).append(attrs.get("value", ""))
        elif tag == "select" and name:
            self.select, self.first_option, self.selected = name, None, False
        elif tag == "option" and self.select:
            value = attrs.get("value", "")
            if self.first_option is None:
                self.first_option = value
            if "selected" in attrs:
                self.data[self.select] = [value]
                self.selected = True
        elif tag == "textarea" and name:
            self.textarea = name
            self.data[name] = [""]

    def handle_data(self, data) -> None:
        if self.inside and self.textarea:
            self.data[self.textarea][0] += data

    def handle_endtag(self, tag) -> None:
        if tag == "select" and self.select:
            if not self.selected and self.first_option is not None:
                self.data[self.select] = [self.first_option]
            self.select = None
        elif tag == "textarea":
            self.textarea = None
        elif tag == "form" and self.inside:
            self.inside = False


@pytest.fixture
def queued(monkeypatch):
    from reports import services

    calls = []
    monkeypatch.setattr(services, "queue_build", calls.append)
    return calls


def test_step_one_is_one_list_with_descriptions_and_the_old_ways_are_gone(director, login) -> None:
    html = login(director).get(reverse("reports:list")).content.decode()
    for step in ("What do you want to make?", "For which period?", "Anything to add?"):
        assert step in html
    for choice in hub.kind_choices(None):
        assert choice.label in html and choice.help.replace("'", "&#x27;") in html
    assert html.count('id="report-generate"') == 1
    # One way to choose: no report type cards, no "Main" badges, no example chips, no "What report do you need?" box.
    for gone in ('name="report_type" value="participation"', ">Main<", "report-example", "What report do you need?",
                 'name="request_text"', "report-summary", "More settings"):
        assert gone not in html
    assert '<details id="report-options"' in html and '<details id="report-options" class="group rounded-2xl border border-line" open' not in html
    with pytest.raises(NoReverseMatch):
        reverse("reports:fill")


def test_figures_defaults_generate_at_once(director, login, queued, clock) -> None:
    """Click 1: Reports, click 2: the kind (the default here), click 3: Generate: the current quarter, PDF, every section."""
    clock(datetime(2026, 11, 26, 10, 0))
    client = login(director)
    html = client.get(reverse("reports:list") + "?kind=figures-participation").content.decode()
    values = FormValues(html, form_id="report-form").data
    assert values["range"] == ["q4"] and values["period_year"] == ["2026"] and values["format"] == ["pdf"]
    response = client.post(reverse("reports:list"), values)
    assert response.status_code == 302
    report = GeneratedReport.objects.get()
    assert report.report_type == "participation" and report.format == "pdf"
    assert (report.date_range_start, report.date_range_end) == (date(2026, 10, 1), date(2026, 12, 31))
    from reports.content import REPORT_TYPES

    sections = set(report.included_sections.strip("[]").replace('"', "").replace(" ", "").split(","))
    assert set(REPORT_TYPES["participation"].default_sections) - {"ai_summary"} <= sections


def test_the_ai_summary_is_on_by_default_only_with_the_ai(director, login, settings, monkeypatch) -> None:
    from assistant import gemini

    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    html = login(director).get(reverse("reports:list")).content.decode()
    assert FormValues(html, form_id="report-form").data.get("ai_summary") == ["on"]
    monkeypatch.setattr(gemini, "is_configured", lambda: False)
    html = login(director).get(reverse("reports:list")).content.decode()
    assert "ai_summary" not in FormValues(html, form_id="report-form").data
    assert "notes and files are not used" in html


def test_activity_report_defaults_to_the_latest_conducted_activity(director, login, make_activity, monkeypatch) -> None:
    from reports import generator

    monkeypatch.setattr(generator, "_queue", lambda version: None)
    make_activity("Older forum", start=date(2026, 3, 10))
    newest = make_activity("Newest forum", start=date(2026, 9, 2))
    make_activity("Still a draft", status="Draft", start=date(2026, 12, 1))
    client = login(director)
    html = client.get(reverse("reports:list") + "?kind=activity_report").content.decode()
    assert "For which activity?" in html
    values = FormValues(html, action=reverse("reports:generator")).data
    assert values["activity"] == [str(newest.pk)] and values["kind"] == ["activity_report"]
    response = client.post(reverse("reports:generator"), values)
    assert response.status_code == 302
    assert ReportDraft.objects.get().activity == newest


def test_accomplishment_report_defaults_to_this_quarter_and_a_program_name(director, login, clock) -> None:
    clock(datetime(2026, 8, 5, 10, 0))
    html = login(director).get(reverse("reports:list") + "?kind=accomplishment_report").content.decode()
    values = FormValues(html, action=reverse("reports:generator")).data
    assert values["preset"] == ["q3"] and values["year"] == ["2026"]
    assert values["program_name"] == [hub.DEFAULT_PROGRAM_NAME]


def test_the_activity_tab_opens_the_page_with_steps_one_and_two_filled(director, login, make_activity) -> None:
    activity = make_activity("Seaweed training")
    make_activity("Another one", start=date(2026, 9, 1))
    url = hub.kind_url("evaluation_summary", activity)
    html = login(director).get(url).content.decode()
    assert 'aria-current="true"' in html and 'data-kind="evaluation_summary" aria-current="true"' in html
    values = FormValues(html, action=reverse("reports:generator")).data
    assert values["activity"] == [str(activity.pk)] and values["for_activity"] == [str(activity.pk)]


def test_attendance_forms_on_the_general_page_pick_an_activity(director, login, make_activity, register,
                                                               fake_pdf_renderer) -> None:
    from registrations.attendance_forms import NO_ATTENDANCE

    activity = make_activity("Pitch clinic", start=date(2026, 9, 2))
    client = login(director)
    html = client.get(reverse("reports:list") + "?kind=form-confirmation").content.decode()
    values = FormValues(html, action=reverse("reports:attendance_form")).data
    assert values["activity"] == [str(activity.pk)] and values["kind"] == ["form-confirmation"]
    response = client.post(reverse("reports:attendance_form"), {"activity": activity.pk, "kind": "form-attendance"},
                           follow=True)
    assert NO_ATTENDANCE in response.content.decode()  # the generator's own refusal, as a message


def test_attendance_form_needs_the_permission(view_only, login, make_activity) -> None:
    activity = make_activity("Pitch clinic")
    response = login(view_only).post(reverse("reports:attendance_form"), {"activity": activity.pk,
                                                                          "kind": "form-attendance"})
    assert response.status_code == 403
    assert login(view_only).post(reverse("reports:attendance_form"), {"activity": activity.pk,
                                                                      "kind": "nope"}).status_code in (403, 404)
