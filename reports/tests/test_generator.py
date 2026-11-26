"""The report generator (ASSUMPTIONS 442-445): drafts, versions, the numbers rule, exports, save and submit. Fake Gemini
and a fake PDF renderer only (the root conftest); numbers never come from the model's text; only photos from
photos_for_reports; the DOCX opens; without the Gemini key everything except the AI text still works."""

import io
import json
import logging
from datetime import date, datetime, time
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import Position
from activities.models import ActivityStatus, BudgetCategory, BudgetLine, ParticipantCategory
from approvals.models import Submission
from assistant.gemini import AssistantUnavailable
from assistant.testing import FakeGemini
from certificates.models import CertificateSignatory
from certificates.testing import FakeRenderer
from documents.models import Document, DocumentType
from registrations.models import AttendanceDay, EvaluationAttachment, Registration
from reports import draft_export, draft_facts, generator
from reports.models import ReportAttachment, ReportDraft, ReportDraftVersion

from .conftest import TEST_NOW, manila

pytestmark = pytest.mark.django_db
Kind = ReportDraft.Kind


def png(color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(out, "PNG")
    return out.getvalue()


def blank_pdf(pages: int = 1) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(595, 842)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class PageRenderer(FakeRenderer):
    """Records every HTML like FakeRenderer but returns a real one-page PDF, so the annex can be merged with pypdf."""

    def render(self, html: str) -> bytes:
        super().render(html)
        return blank_pdf()


@pytest.fixture
def renderer(monkeypatch) -> PageRenderer:
    from certificates import pdf

    fake = PageRenderer()
    monkeypatch.setattr(pdf, "_override", fake)
    return fake


@pytest.fixture
def staff(make_member):
    member = make_member("Admin Staff")
    member.position = Position.objects.create(name="Project Development Officer I")
    member.save(update_fields=["position"])
    return member


@pytest.fixture
def workshop(make_activity, director):
    """A Conducted 2-day workshop with budget lines, a resource person, attendance on both days (names), a cancelled
    registration and a walk-in."""
    from activities.models import ActivityResourcePerson, ResourcePerson

    activity = make_activity("Pitching Workshop", start=date(2026, 10, 20), end=date(2026, 10, 21),
                             categories=("Faculty", "Incubatees", "MSMEs"))
    category = BudgetCategory.objects.get_or_create(name="Meals/Snacks")[0]
    BudgetLine.objects.create(activity=activity, category=category, particulars="Lunch", quantity=Decimal("30"),
                              unit="pax", days=2, unit_price=Decimal("150.00"), line_total=Decimal("9000.00"))
    activity.budget_amount = Decimal("9000.00")
    activity.save(update_fields=["budget_amount"])
    person = ResourcePerson.objects.create(name="Engr. Maria Santos", email="maria@example.com")
    ActivityResourcePerson.objects.create(activity=activity, resource_person=person, topic="Pitch decks")
    from registrations.models import FormTemplate, FormType

    form = FormTemplate.objects.create(activity=activity, form_type=FormType.REGISTRATION, title="Reg", created_by=director)
    people = [("Ana Reyes", "Incubatees", [date(2026, 10, 20), date(2026, 10, 21)], None, "online"),
              ("Ben Cruz", "Faculty", [date(2026, 10, 20)], None, "online"),
              ("Carla Lim", "MSMEs", [], TEST_NOW, "online"),
              ("Dan Uy", "MSMEs", [date(2026, 10, 21)], None, "walk_in")]
    for name, category_name, days, cancelled, source in people:
        registration = Registration.objects.create(
            activity=activity, template=form, participant_category=ParticipantCategory.objects.get(name=category_name),
            name=name, email=f"{name.split()[0].lower()}@example.com", designation="Owner", office_agency="DTI",
            attendance_confirmed=bool(days), cancelled_at=cancelled, registration_source=source)
        for day in days:
            AttendanceDay.objects.create(registration=registration, day=day, checked_in_at=manila(day, 8),
                                         created_at=manila(day, 8), method=AttendanceDay.Method.QR)
    return activity


def reply(**sections) -> FakeGemini:
    return FakeGemini.say(json.dumps({"sections": sections}))


def make_draft(member, activity, **extra) -> ReportDraft:
    return generator.create_draft(member, kind=extra.pop("kind", Kind.ACTIVITY_REPORT), activity_id=activity.pk, **extra)


def write(draft) -> ReportDraftVersion:
    version = generator.latest_version(draft)
    generator.generate_version(version.pk)
    version.refresh_from_db()
    return version


GOOD = dict(highlights="The workshop on [[period_covered]] trained [[attended]] participants with Engr. Maria Santos.",
            accomplishments="Pitch decks improved\nMentors matched", problems="None reported.",
            recommendations="Start registration earlier\nInvite more MSMEs")


# --- Access -------------------------------------------------------------------------------------------------------------


def test_only_members_who_create_activities(workshop, view_only, client) -> None:
    with pytest.raises(PermissionDenied):
        make_draft(view_only, workshop)
    client.force_login(view_only)
    # The old address redirects to the one Reports page (ASSUMPTIONS 461), which shows no form without the permission;
    # posting a draft is still refused.
    assert client.get(reverse("reports:generator")).status_code == 302
    assert "data-report-kinds" not in client.get(reverse("reports:list")).content.decode()
    assert client.post(reverse("reports:generator"), {"kind": "activity_report", "activity": workshop.pk}).status_code == 403


def test_a_report_assignment_opens_the_generator(workshop, director, view_only, monkeypatch) -> None:
    from approvals import assignments

    view_only.role.can_create_activity = False
    assignments.assign_work(director, "report", view_only, report_title="Q3 report")
    draft = make_draft(view_only, workshop)
    assert draft.created_by == view_only


def test_a_draft_is_only_its_makers(workshop, staff, director, client) -> None:
    draft = make_draft(staff, workshop)
    with pytest.raises(Http404):
        generator.get_draft(director, draft.pk)
    client.force_login(director)
    assert client.get(reverse("reports:generator_draft", args=[draft.pk])).status_code == 404
    version = generator.latest_version(draft)
    assert client.get(reverse("reports:generator_export", args=[version.pk, "pdf"])).status_code == 404


# --- Creating -------------------------------------------------------------------------------------------------------------


def test_the_scope_rules(workshop, staff) -> None:
    with pytest.raises(ValidationError, match="Choose the activity"):
        generator.create_draft(staff, kind=Kind.ACTIVITY_REPORT, start="2026-01-01", end="2026-06-30")
    with pytest.raises(ValidationError, match="OR a period"):
        generator.create_draft(staff, kind=Kind.EVALUATION_SUMMARY, activity_id=workshop.pk, start="2026-01-01", end="2026-01-31")
    with pytest.raises(ValidationError, match="end date"):
        generator.create_draft(staff, kind=Kind.OTHER, start="2026-06-30", end="2026-01-01")
    draft = generator.create_draft(staff, kind=Kind.EVALUATION_SUMMARY, start="2026-01-01", end="2026-06-30")
    assert draft.activity is None and draft.title.startswith("Evaluation Summary – January 1")


def test_files_are_checked_before_anything_is_stored(workshop, staff, settings) -> None:
    import os

    bad_photo = SimpleUploadedFile("notes.pdf", blank_pdf())
    with pytest.raises(ValidationError, match="JPG or PNG"):
        make_draft(staff, workshop, files=[generator.NewFile(bad_photo, ReportAttachment.Kind.PHOTO)])
    fake = SimpleUploadedFile("fake.pdf", b"not a pdf at all")
    with pytest.raises(ValidationError):
        make_draft(staff, workshop, files=[generator.NewFile(fake, ReportAttachment.Kind.DOCUMENT)])
    settings.REPORT_GENERATOR_MAX_ATTACHMENTS = 1
    two = [generator.NewFile(SimpleUploadedFile(f"p{n}.png", png()), ReportAttachment.Kind.PHOTO) for n in range(2)]
    with pytest.raises(ValidationError, match="At most 1"):
        make_draft(staff, workshop, files=two)
    assert not ReportDraft.objects.exists()
    folder = os.path.join(settings.MEDIA_ROOT, "report-drafts")
    assert not os.path.exists(folder) or not any(files for _, _, files in os.walk(folder))


def test_the_first_version_is_queued_after_the_commit(workshop, staff, monkeypatch, django_capture_on_commit_callbacks) -> None:
    queued = []
    monkeypatch.setattr("django_q.tasks.async_task", lambda *args, **kwargs: queued.append(args))
    with django_capture_on_commit_callbacks(execute=True):
        draft = make_draft(staff, workshop, message="Partners: DTI Agusan.")
    version = generator.latest_version(draft)
    assert queued == [(generator.TASK, version.pk)] and version.state == ReportDraftVersion.State.PENDING
    assert draft.messages.get().content == "Partners: DTI Agusan."


# --- Writing a version: the numbers rule ---------------------------------------------------------------------------------


def test_numbers_come_from_the_system_not_the_model(workshop, staff, fake_gemini) -> None:
    fake_gemini.replies.append(reply(**GOOD))
    draft = make_draft(staff, workshop)
    version = write(draft)
    assert version.state == ReportDraftVersion.State.READY
    text = version.sections["highlights"]["text"]
    # [[attended]] is filled from the database: Ana, Ben, Dan (Carla cancelled) = 3, never the model's own number.
    assert "trained 3 participants" in text and "October 20–21, 2026" in text
    assert version.sections["highlights"]["ai"] is True and version.sections["highlights"]["unverified"] == []
    facts = version.facts
    assert facts["counts"]["attended"] == 3 and facts["counts"]["cancelled"] == 1 and facts["counts"]["walk_ins"] == 1
    assert facts["budget"]["total"] == "₱9,000.00" and facts["budget"]["rows"][0]["quantity"].startswith("30")
    assert [d["attended"] for d in facts["days"]] == [2, 2]
    assert generator.export_refused_reason(version) is None


def test_a_number_the_model_made_up_blocks_the_export_until_edited(workshop, staff, fake_gemini) -> None:
    fake_gemini.replies.append(reply(**{**GOOD, "highlights": "About 45 participants came; 12% were Faculty."}))
    draft = make_draft(staff, workshop)
    version = write(draft)
    assert version.sections["highlights"]["unverified"] == ["45", "12%"]
    assert "Check the numbers" in generator.export_refused_reason(version)
    with pytest.raises(generator.GeneratorError, match="Check the numbers"):
        generator.export_pdf(staff, version)
    edited = generator.edit_section(staff, version, "highlights", "Three participants attended both days.")
    assert edited.number == 2 and edited.source == ReportDraftVersion.Source.EDITED
    assert edited.sections["highlights"] == {"text": "Three participants attended both days.", "ai": False, "unverified": []}
    assert generator.export_refused_reason(edited) is None
    assert edited.facts == version.facts


def test_unknown_placeholders_are_flagged(workshop, staff, fake_gemini) -> None:
    fake_gemini.replies.append(reply(**{**GOOD, "problems": "Lunch cost [[lunch_cost]]."}))
    version = write(make_draft(staff, workshop))
    assert version.sections["problems"]["unverified"] == ["[[lunch_cost]]"]


def test_participant_names_and_photos_never_go_to_the_ai(workshop, staff, fake_gemini, director) -> None:
    fake_gemini.replies.append(reply(**GOOD))
    photo = generator.NewFile(SimpleUploadedFile("group.png", png()), ReportAttachment.Kind.PHOTO)
    version = write(make_draft(staff, workshop, files=[photo]))
    prompt = fake_gemini.generate_calls[0]["turns"][0].text
    assert not any(name in prompt for name in ("Ana Reyes", "Ben Cruz", "Carla Lim", "Dan Uy"))
    assert "group.png" not in prompt and not fake_gemini.read_calls  # photos are never read
    assert "Engr. Maria Santos" in prompt and "Pitch decks" in prompt  # resource persons and topics may be named
    assert version.ai_prompt.startswith("[System instruction]")


def test_document_attachments_and_activity_files_are_read(workshop, staff, fake_gemini, director) -> None:
    from assistant.models import DocumentEmbedding
    from documents import services as document_services

    document = document_services.upload_document(director, workshop, DocumentType.objects.get(name="Request Letter"),
                                                 SimpleUploadedFile("letter.pdf", blank_pdf()), "Venue request")
    DocumentEmbedding.objects.create(document=document, chunk_index=0, chunk_text="The venue is the CSU Hall.",
                                     embedding_vector=[0.0] * 768)
    fake_gemini.ocr_text = "Program: opening, two sessions, closing."
    program = generator.NewFile(SimpleUploadedFile("program.png", png()), ReportAttachment.Kind.DOCUMENT)
    fake_gemini.replies.append(reply(**GOOD))
    write(make_draft(staff, workshop, files=[program]))
    prompt = fake_gemini.generate_calls[0]["turns"][0].text
    assert "The venue is the CSU Hall." in prompt and "Program: opening, two sessions" in prompt
    assert ReportAttachment.objects.get().text_status == ReportAttachment.TextStatus.READ


def test_without_the_key_everything_but_the_ai_text_works(workshop, staff, monkeypatch, settings, renderer) -> None:
    from assistant import gemini

    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = ""
    draft = make_draft(staff, workshop)
    version = write(draft)
    assert version.state == ReportDraftVersion.State.READY and version.ai_prompt is None
    assert all(value["text"] == "" and not value["ai"] for value in version.sections.values())
    assert generator.AI_OFF_NOTE in draft.messages.order_by("-message_id").first().content
    data, name = generator.export_docx(staff, version)
    assert name.endswith("-v1.docx") and data[:2] == b"PK"
    pdf, _ = generator.export_pdf(staff, version)
    assert pdf.startswith(b"%PDF-") and "₱9,000.00" in renderer.rendered[-1]  # the main page renders after the annex


def test_an_ai_failure_fails_the_version_and_try_again_makes_a_new_one(workshop, staff, fake_gemini, monkeypatch) -> None:
    def busy(**kwargs):
        raise AssistantUnavailable("The AI service is busy.", "rate_limit")

    monkeypatch.setattr(fake_gemini, "generate", busy)
    draft = make_draft(staff, workshop)
    version = write(draft)
    assert version.state == ReportDraftVersion.State.FAILED and "busy" in version.error
    again = generator.try_again(staff, draft)
    assert again.number == 2 and again.state == ReportDraftVersion.State.PENDING


def test_an_unreadable_answer_fails_in_plain_words(workshop, staff, fake_gemini, caplog) -> None:
    fake_gemini.replies.append(FakeGemini.say("Sure! Here is your report about Ana Reyes."))
    with caplog.at_level(logging.INFO):
        version = write(make_draft(staff, workshop))
    assert version.error == "The AI's answer could not be read." and "Ana Reyes" not in caplog.text


def test_a_revision_sends_the_current_draft_and_waits_its_turn(workshop, staff, fake_gemini) -> None:
    fake_gemini.replies += [reply(**GOOD), reply(**{**GOOD, "highlights": "Shorter."})]
    draft = make_draft(staff, workshop)
    write(draft)
    second = generator.request_revision(staff, draft, "Make the highlights shorter.")
    with pytest.raises(generator.GeneratorError, match="being written"):
        generator.request_revision(staff, draft, "And another change.")
    generator.generate_version(second.pk)
    second.refresh_from_db()
    prompt = json.loads(fake_gemini.generate_calls[1]["turns"][0].text)
    assert prompt["REQUEST"] == "Make the highlights shorter." and "CURRENT DRAFT" in prompt
    assert second.sections["highlights"]["text"] == "Shorter." and second.source == ReportDraftVersion.Source.REVISED
    assert list(draft.versions.values_list("number", flat=True)) == [1, 2]  # every version is kept
    with pytest.raises(generator.GeneratorError, match="newest"):
        generator.edit_section(staff, generator.get_version(staff, draft.versions.get(number=1).pk), "problems", "x")


# --- Exports ----------------------------------------------------------------------------------------------------------


def test_the_docx_opens_and_has_the_activity_report_parts(workshop, staff, director, fake_gemini) -> None:
    from docx import Document as Docx

    CertificateSignatory.objects.create(name="Dr. Juan Dela Cruz", position="TBI Director", member=director)
    fake_gemini.replies.append(reply(**GOOD))
    data, _ = generator.export_docx(staff, write(make_draft(staff, workshop, options={"office_line": "Office of the VP"})))
    document = Docx(io.BytesIO(data))
    text = "\n".join(p.text for p in document.paragraphs)
    cells = "\n".join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
    assert "ACTIVITY REPORT" in text and "Office of the VP" in text and "A. Highlights of the Activity" in text
    assert "B. Problems/Constraints Met" in text and "C. Running Budget" in text and "D. Recommendation(s)" in text
    assert "₱9,000.00" in cells and "DR. JUAN DELA CRUZ" in cells and "Project Development Officer I" in cells
    assert "Ana Reyes" in cells and "Will not attend" in cells  # the annex name lists (owner decision)
    assert generator.AI_LABEL not in text + cells
    assert document.sections[0].footer.paragraphs[0].text == "F-COR-001 Rev. 3 10/06/2023"


def test_the_pdf_has_the_layout_the_annex_and_no_ai_label(workshop, staff, fake_gemini, renderer) -> None:
    from pypdf import PdfReader

    fake_gemini.replies.append(reply(**GOOD))
    data, _ = generator.export_pdf(staff, write(make_draft(staff, workshop)))
    html = renderer.rendered[-1]
    assert "ACTIVITY REPORT" in html and "Period Covered:" in html and "Implementing Unit:" in html
    assert "F-COR-001 Rev. 3 10/06/2023" in html and "Noted:" in html and "Prepared by:" in html
    assert generator.AI_LABEL not in html and "AI-generated" not in html
    # The generated Attendance and Confirmation forms (item 434) were rendered in memory and appended.
    assert len(renderer.rendered) == 3 and "ATTENDANCE" in renderer.rendered[0].upper()
    assert len(PdfReader(io.BytesIO(data)).pages) == 3
    assert not Document.objects.filter(activity=workshop).exists()  # in memory only, never saved


def test_photos_six_a_page_and_only_ticked_participant_photos(workshop, staff, fake_gemini, renderer) -> None:
    from registrations.models import EvaluationSubmission, FormTemplate, FormType

    registration = Registration.objects.get(name="Ana Reyes")
    form = FormTemplate.objects.create(activity=workshop, form_type=FormType.EVALUATION, title="Eval", created_by=workshop.created_by)
    submission = EvaluationSubmission.objects.create(template=form, registration=registration, certificate_name="Ana",
                                                     certificate_email="ana@example.com")
    ticked, unticked = [EvaluationAttachment.objects.create(
        submission=submission, file_path=ContentFile(png(), name=f"evaluation-photos/p{n}.png"), file_name=f"p{n}.png",
        file_size_kb=1, content_type="image/png", use_in_reports=use, uploaded_at=TEST_NOW,
        marked_at=TEST_NOW if use else None) for n, use in ((1, True), (2, False))]
    photos = [generator.NewFile(SimpleUploadedFile(f"g{n}.png", png()), ReportAttachment.Kind.PHOTO) for n in range(6)]
    fake_gemini.replies.append(reply(**GOOD))
    version = write(make_draft(staff, workshop, files=photos))
    sources = [(p["source"], p["id"]) for p in version.facts["photos"]]
    assert ("evaluation", ticked.pk) in sources and ("evaluation", unticked.pk) not in sources and len(sources) == 7
    generator.export_pdf(staff, version)
    assert renderer.rendered[-1].count("Photo Documentation") == 2  # 7 photos: 6 + 1


def test_numbers_in_the_export_are_the_facts(workshop, staff, fake_gemini, renderer) -> None:
    fake_gemini.replies.append(reply(**GOOD))
    version = write(make_draft(staff, workshop))
    Registration.objects.filter(activity=workshop).update(attendance_confirmed=False)  # later changes never alter a version
    generator.export_pdf(staff, version)
    assert "trained 3 participants" in renderer.rendered[-1]


# --- Save and submit --------------------------------------------------------------------------------------------------


def test_save_to_the_activitys_documents_once(workshop, staff, fake_gemini, renderer) -> None:
    fake_gemini.replies.append(reply(**GOOD))
    version = write(make_draft(staff, workshop))
    document = generator.save_to_documents(staff, version)
    assert document.document_type.name == "Activity Report" and document.status.status_name == "Approved"
    assert document.reviewed_by is None and document.file_extension == "pdf"  # an approved copy (item 432)
    with pytest.raises(generator.GeneratorError, match="already saved"):
        generator.save_to_documents(staff, ReportDraftVersion.objects.get(pk=version.pk))


def test_a_period_report_cannot_be_saved_to_an_activity(staff, fake_gemini, renderer, workshop) -> None:
    fake_gemini.replies.append(reply(overview="x", open_answers="y", recommendations="z"))
    draft = generator.create_draft(staff, kind=Kind.EVALUATION_SUMMARY, start="2026-10-01", end="2026-10-31")
    with pytest.raises(generator.GeneratorError, match="one activity"):
        generator.save_to_documents(staff, write(draft))


def test_submit_to_the_director_once(workshop, staff, director, fake_gemini, renderer) -> None:
    fake_gemini.replies.append(reply(**GOOD))
    version = write(make_draft(staff, workshop))
    submission = generator.submit_to_director(staff, version, "For your signature.")
    assert submission.status == Submission.Status.SUBMITTED and submission.file_extension == "pdf"
    assert submission.activity == workshop and submission.note == "For your signature."
    with pytest.raises(generator.GeneratorError, match="already submitted"):
        generator.submit_to_director(staff, ReportDraftVersion.objects.get(pk=version.pk))


# --- Evaluation Summary --------------------------------------------------------------------------------------------------


def test_evaluation_summary_numbers_and_open_answers(workshop, staff, fake_gemini, evaluate, renderer) -> None:
    ana, ben = Registration.objects.get(name="Ana Reyes"), Registration.objects.get(name="Ben Cruz")
    evaluate(ana, {"Overall rating": 5}, comments="Great mentors")
    evaluate(ben, {"Overall rating": 4}, comments="More time for pitching")
    fake_gemini.replies.append(reply(overview="Ratings averaged [[question_1_mean]].", open_answers="Praise for mentors.",
                                     recommendations="Longer pitching practice"))
    version = write(make_draft(staff, workshop, kind=Kind.EVALUATION_SUMMARY))
    facts = version.facts
    assert facts["counts"]["evaluations"] == 2 and facts["counts"]["response_rate"] == "66.7%"
    assert facts["ratings"][0]["mean"] == "4.50" and [f["count"] for f in facts["ratings"][0]["frequency"]] == [1, 1, 0, 0, 0]
    assert version.sections["overview"]["text"] == "Ratings averaged 4.50."
    prompt = fake_gemini.generate_calls[0]["turns"][0].text
    assert "Great mentors" in prompt and "Ana Reyes" not in prompt  # open answers without names
    generator.export_pdf(staff, version)
    assert "EVALUATION SUMMARY REPORT" in renderer.rendered[-1]


def test_period_evaluation_summary(workshop, staff, fake_gemini, evaluate, make_activity) -> None:
    other = make_activity("Forum", start=date(2026, 10, 5))
    evaluate(Registration.objects.get(name="Ana Reyes"), {"Overall rating": 5})
    fake_gemini.replies.append(reply(overview="x", open_answers="y", recommendations="z"))
    draft = generator.create_draft(staff, kind=Kind.EVALUATION_SUMMARY, start="2026-10-01", end="2026-10-31")
    facts = write(draft).facts
    assert facts["scope"] == "period" and facts["counts"]["activities"] == 2
    assert [a["title"] for a in facts["activities"]] == ["Forum", "Pitching Workshop"] and other.pk


# --- Prepared by / Noted ---------------------------------------------------------------------------------------------


def test_noted_is_the_directors_linked_signature(director, make_member) -> None:
    assert draft_facts.noted_by() == {"name": "", "position": "TBI Director"}
    CertificateSignatory.objects.create(name="Dr. Juan Dela Cruz", position="Director, CSU-TBI", member=director)
    assert draft_facts.noted_by() == {"name": "Dr. Juan Dela Cruz", "position": "Director, CSU-TBI"}


# --- Pages ------------------------------------------------------------------------------------------------------------


def test_pages_render_with_the_compact_chat_bar(workshop, staff, fake_gemini, client) -> None:
    fake_gemini.replies.append(reply(**{**GOOD, "highlights": "About 45 came."}))
    draft = make_draft(staff, workshop)
    write(draft)
    client.force_login(staff)
    listing = client.get(reverse("reports:generator"), follow=True).content.decode()  # -> /reports/?kind=activity_report
    assert "Write the draft" in listing and draft.title in listing and "What do you want to make?" in listing
    html = client.get(reverse("reports:generator_draft", args=[draft.pk])).content.decode()
    assert 'class="chat-bar"' in html and 'id="gen-send"' in html and 'rows="1"' in html
    assert generator.AI_LABEL in html and "Not from the system: 45" in html
    assert "Save as a new version" in html


def test_creating_through_the_page(workshop, staff, client, monkeypatch) -> None:
    monkeypatch.setattr("django_q.tasks.async_task", lambda *a, **k: None)
    client.force_login(staff)
    response = client.post(reverse("reports:generator"), {
        "kind": "activity_report", "scope": "period", "activity": workshop.pk, "message": "Notes",
        "photos": [SimpleUploadedFile("a.png", png())]})
    draft = ReportDraft.objects.get()
    assert response.status_code == 302 and draft.activity == workshop  # the Activity Report is always one activity
    assert draft.attachments.get().kind == ReportAttachment.Kind.PHOTO
    response = client.post(reverse("reports:generator"), {"kind": "activity_report"})
    assert response.status_code == 400 and "Choose the activity" in response.content.decode()


def test_the_sidebar_item_and_export_responses(workshop, staff, view_only, fake_gemini, client, renderer) -> None:
    client.force_login(staff)
    home = client.get(reverse("home")).content.decode()
    assert reverse("reports:list") in home and "Report generator" not in home  # one Reports item (ASSUMPTIONS 461)
    fake_gemini.replies.append(reply(**GOOD))
    version = write(make_draft(staff, workshop))
    response = client.get(reverse("reports:generator_export", args=[version.pk, "docx"]))
    assert response["Content-Disposition"].startswith("attachment;") and response["Cache-Control"] == "private, no-store"
    client.force_login(view_only)
    assert reverse("reports:generator") not in client.get(reverse("home")).content.decode()  # no sidebar item at all


def test_deleting_the_activity_counts_and_removes_the_drafts(workshop, staff, make_member,
                                                            django_capture_on_commit_callbacks) -> None:
    import os

    from activities.deletion import delete_activity, deletion_summary, what_will_be_deleted

    draft = make_draft(staff, workshop, files=[generator.NewFile(SimpleUploadedFile("a.png", png()), ReportAttachment.Kind.PHOTO)])
    path = draft.attachments.get().file_path.path
    summary = deletion_summary(workshop)
    assert summary.report_drafts == 1 and summary.counts()["report_drafts"] == 1
    assert "1 report draft (with their versions and files)" in what_will_be_deleted(summary)
    with django_capture_on_commit_callbacks(execute=True):
        delete_activity(make_member("System Administrator"), workshop, workshop.title)
    assert not ReportDraft.objects.exists() and not os.path.exists(path)
