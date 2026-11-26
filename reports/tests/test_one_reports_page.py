"""The ONE Reports page (ASSUMPTIONS 461) and the activity's Reports tab (ASSUMPTIONS 462): one list of what to make
(figures reports + the report generator's kinds; for one activity only the per-activity kinds and the attendance
forms), notes and files that guide the figures report's AI summary (never a number), one list of past reports, the old
generator address redirecting. GENERATED_REPORT and REPORT_DRAFT stay two tables (REPORT_ATTACHMENT belongs to exactly
one of them). Fake Gemini and a fake PDF renderer only; fixed dates (TEST_NOW)."""

import io
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse

from accounts.models import Position
from reports import generator, hub, narrative, services
from reports.models import GeneratedReport, ReportAttachment, ReportDraft

from .conftest import TEST_NOW

pytestmark = pytest.mark.django_db
Kind = ReportDraft.Kind


@pytest.fixture
def staff(make_member):
    member = make_member("Admin Staff")
    member.position = Position.objects.create(name="Project Development Officer I")
    member.save(update_fields=["position"])
    return member


@pytest.fixture
def queued(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(services, "queue_build", calls.append)
    return calls


@pytest.fixture
def conducted(make_activity):
    return make_activity("Pitching Workshop", start=date(2026, 3, 10), categories=("Faculty", "MSMEs"))


def docx_bytes(text: str) -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def request(member, files=None, **data) -> GeneratedReport:
    payload = {"report_type": "accomplishment", "format": "pdf", "date_range_start": "2026-01-01",
               "date_range_end": "2026-12-31"}
    payload.update(data)
    return services.request_report(member, payload, now=TEST_NOW, files=files)


# --- One list of what to make ----------------------------------------------------------------------------------------


def test_the_page_offers_figures_and_written_kinds() -> None:
    keys = [choice.key for choice in hub.kind_choices(None)]
    assert keys[:5] == ["figures-accomplishment", "figures-participation", "figures-evaluation",
                        "figures-document_compliance", "figures-certificates"]
    assert set(keys[5:]) == set(Kind.values)  # every generator kind, once
    assert hub.FORM_ATTENDANCE not in keys  # the attendance forms need one activity


def test_one_activity_gets_only_the_per_activity_kinds(conducted) -> None:
    keys = [choice.key for choice in hub.kind_choices(conducted)]
    assert keys == [Kind.ACTIVITY_REPORT, Kind.EVALUATION_SUMMARY, hub.FORM_ATTENDANCE, hub.FORM_CONFIRMATION,
                    Kind.REQUEST_LETTER, Kind.ACTIVITY_DESIGN, Kind.OTHER]
    assert not any(key.startswith(hub.FIGURES_PREFIX) for key in keys)
    assert Kind.ACCOMPLISHMENT_REPORT not in keys


def test_choose_kind_falls_back_to_the_default(conducted) -> None:
    assert hub.choose_kind("figures-participation", None) == "figures-participation"
    assert hub.choose_kind("nonsense", None) == hub.DEFAULT_KIND
    assert hub.choose_kind(None, conducted) == Kind.ACTIVITY_REPORT
    assert hub.choose_kind("figures-participation", conducted) == Kind.ACTIVITY_REPORT  # not offered for one activity
    assert hub.group_of(hub.FORM_CONFIRMATION) == "forms" and hub.group_of(Kind.OTHER) == "written"


@pytest.mark.parametrize("kind, marker", [
    ("figures-participation", 'name="report_type"'),
    (Kind.ACTIVITY_REPORT, 'name="kind"'),
    (Kind.REQUEST_LETTER, "Type the details (no activity yet)"),
])
def test_each_kind_shows_its_own_form(director, login, kind, marker) -> None:
    html = login(director).get(reverse("reports:list") + f"?kind={kind}").content.decode()
    assert "data-report-kinds" in html and f'data-kind="{kind}" aria-current="true"' in html
    assert marker in html


def test_the_attendance_forms_are_offered_for_one_activity(director, login, conducted) -> None:
    html = login(director).get(reverse("reports:list") + f"?kind={hub.FORM_ATTENDANCE}&activity={conducted.pk}"
                               ).content.decode()
    assert "data-attendance-forms" in html and "Back to the activity" in html


def test_without_the_permission_the_page_lists_but_offers_nothing(view_only, login) -> None:
    html = login(view_only).get(reverse("reports:list")).content.decode()
    assert "data-report-kinds" not in html and "Past reports" in html


def test_one_sidebar_item(staff, login) -> None:
    home = login(staff).get(reverse("home")).content.decode()
    assert reverse("reports:list") in home and "Report generator" not in home


# --- The old generator address ---------------------------------------------------------------------------------------


def test_the_old_generator_address_redirects(director, login, conducted) -> None:
    client = login(director)
    response = client.get(reverse("reports:generator"))
    assert response.status_code == 302 and response.url == f"{reverse('reports:list')}?kind=activity_report"
    response = client.get(reverse("reports:generator") + f"?start_kind=evaluation_summary&activity={conducted.pk}")
    assert response.url == f"{reverse('reports:list')}?kind=evaluation_summary&activity={conducted.pk}"


def test_a_refused_draft_comes_back_on_the_one_page(director, login) -> None:
    response = login(director).post(reverse("reports:generator"), {"kind": Kind.ACTIVITY_REPORT})  # no activity
    assert response.status_code == 400
    html = response.content.decode()
    assert "What do you want to make?" in html and 'data-kind="activity_report" aria-current="true"' in html


# --- Notes and files guide the figures report's AI summary ----------------------------------------------------------------


def test_notes_and_files_are_stored_and_sent_as_data(director, queued, conducted, fake_gemini, fake_pdf_renderer) -> None:
    upload = SimpleUploadedFile("highlights.docx", docx_bytes("Partner: DTI Caraga. Highlight: pitch clinic."))
    report = request(director, files=[generator.NewFile(upload, "document")], notes="Mention the DTI partnership.",
                     included_sections=["summary", "ai_summary"])
    assert report.notes == "Mention the DTI partnership."
    attachment = ReportAttachment.objects.get(generated_report=report)
    assert attachment.draft is None
    fake_gemini.replies.append(fake_gemini.say("The TBI worked with DTI Caraga on a pitch clinic."))
    services.build_report(report.pk, now=TEST_NOW)
    prompt = fake_gemini.generate_calls[0]["turns"][0].text
    assert narrative.GUIDANCE_RULE in prompt
    assert "Mention the DTI partnership." in prompt and "pitch clinic" in prompt
    assert "The TBI worked with DTI Caraga" in fake_pdf_renderer.last_text()


def test_without_notes_or_files_the_prompt_is_unchanged(director, queued, conducted, fake_gemini,
                                                         fake_pdf_renderer) -> None:
    report = request(director, included_sections=["summary", "ai_summary"])
    fake_gemini.replies.append(fake_gemini.say("One workshop was conducted."))
    services.build_report(report.pk, now=TEST_NOW)
    assert narrative.GUIDANCE_RULE not in fake_gemini.generate_calls[0]["turns"][0].text


def test_a_number_not_in_the_data_leaves_the_summary_out(director, queued, conducted, fake_gemini,
                                                          fake_pdf_renderer) -> None:
    report = request(director, notes="We had 450 guests.", included_sections=["summary", "ai_summary"])
    fake_gemini.replies.append(fake_gemini.say("The workshop had 450 guests."))
    services.build_report(report.pk, now=TEST_NOW)
    text = fake_pdf_renderer.last_text()
    assert "450 guests" not in text and "not in the report (450)" in text


def test_notes_are_limited(director, queued) -> None:
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="under 2000"):
        request(director, notes="x" * (services.REPORT_NOTES_MAX + 1))
    assert not GeneratedReport.objects.exists()


def test_a_refused_file_saves_nothing(director, queued) -> None:
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        request(director, files=[generator.NewFile(SimpleUploadedFile("evil.pdf", b"not a pdf"), "document")])
    assert not GeneratedReport.objects.exists() and not ReportAttachment.objects.exists()


def test_the_figures_form_posts_notes_and_files(director, login, queued) -> None:
    upload = SimpleUploadedFile("notes.docx", docx_bytes("Highlights of the year."))
    response = login(director).post(reverse("reports:list"), {
        "report_type": "accomplishment", "format": "pdf", "date_range_start": "2026-01-01",
        "date_range_end": "2026-12-31", "included_sections": ["summary", "ai_summary"],
        "notes": "Say thanks to the partners.", "report_files": [upload]})
    assert response.status_code == 302
    report = GeneratedReport.objects.get()
    assert report.notes == "Say thanks to the partners." and report.attachments.count() == 1


def test_an_attachment_belongs_to_exactly_one_report(director, conducted) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ReportAttachment.objects.create(uploaded_by=director, kind="document", file_path="x.pdf", file_name="x.pdf",
                                        file_extension="pdf", file_size_kb=1)


# --- One list of past reports -----------------------------------------------------------------------------------------


def test_past_reports_lists_every_figures_report_and_only_my_drafts(director, staff, queued, conducted) -> None:
    figures = request(staff)
    mine = generator.create_draft(director, kind=Kind.ACTIVITY_REPORT, activity_id=conducted.pk)
    theirs = generator.create_draft(staff, kind=Kind.ACTIVITY_REPORT, activity_id=conducted.pk)
    GeneratedReport.objects.filter(pk=figures.pk).update(generated_at=TEST_NOW - timedelta(days=1))
    objects = [entry["object"] for entry in services.past_reports(director)]
    assert figures in objects and mine in objects and theirs not in objects
    assert objects.index(mine) < objects.index(figures)  # newest first


def test_one_activitys_past_reports(director, queued, conducted, make_activity, add_document) -> None:
    other = make_activity("Mentoring Day", start=date(2026, 4, 10))
    request(director)
    draft = generator.create_draft(director, kind=Kind.ACTIVITY_REPORT, activity_id=conducted.pk)
    generator.create_draft(director, kind=Kind.ACTIVITY_REPORT, activity_id=other.pk)
    saved = add_document(conducted, "Activity Report", "Approved")
    add_document(conducted, "Activity Design", "Approved")  # not a report
    entries = services.past_reports(director, conducted)
    assert {entry["object"] for entry in entries} == {draft, saved}
    assert {entry["type"] for entry in entries} == {"draft", "document"}


def test_the_page_shows_the_past_reports(director, login, queued, conducted) -> None:
    figures = request(director)
    draft = generator.create_draft(director, kind=Kind.ACTIVITY_REPORT, activity_id=conducted.pk)
    html = login(director).get(reverse("reports:list")).content.decode()
    assert draft.title in html and reverse("reports:generator_draft", args=[draft.pk]) in html
    assert f"Report {figures.pk}" in html or reverse("reports:row", args=[figures.pk]) in html


# --- The activity's Reports tab ---------------------------------------------------------------------------------------


def test_the_activity_reports_tab(director, login, conducted, add_document) -> None:
    saved = add_document(conducted, "Activity Report", "Approved")
    html = login(director).get(f"{reverse('activities:detail', args=[conducted.pk])}?tab=reports").content.decode()
    assert "data-activity-report-kinds" in html
    for key in hub.PER_ACTIVITY:
        assert f"?kind={key}&amp;activity={conducted.pk}" in html
    assert "figures-accomplishment" not in html and "accomplishment_report" not in html
    assert reverse("documents:detail", args=[saved.pk]) in html


def test_the_activity_reports_tab_without_the_permission(view_only, login, conducted) -> None:
    html = login(view_only).get(f"{reverse('activities:detail', args=[conducted.pk])}?tab=reports").content.decode()
    assert "data-activity-report-kinds" not in html and "Past reports" in html
