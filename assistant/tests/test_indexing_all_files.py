"""The assistant reads every file (ASSUMPTIONS 438): documents in every review state, generated reports, approved
submissions; OCR of scans and images with the fake Gemini only; the "Read this scan with AI" tick for scanned
attendance forms; never Event Photos; never OCR in the backfill; re-index on replace, rename (no second OCR), keep on
unapprove; source cards with the state; submissions only for members who may open them."""

import logging
from datetime import date

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse

from activities.models import ActivityAuditLog
from approvals import services as approval_services
from assistant import indexing, pages, services, tools
from assistant.gemini import AssistantUnavailable
from assistant.models import AIChat, AIMessageSource, DocumentEmbedding
from assistant.testing import FakeGemini
from documents import services as document_services
from documents.models import Document, DocumentType
from reports.models import GeneratedReport

from .conftest import PNG_BYTES, docx_file, scanned_pdf, text_pdf

pytestmark = pytest.mark.django_db

OCR_TEXT = "1 | Ana Cruz | Project Officer | DTI Agusan"


def chunks(**link) -> list[str]:
    return list(DocumentEmbedding.objects.filter(**link).order_by("chunk_index").values_list("chunk_text", flat=True))


@pytest.fixture
def chat(director) -> AIChat:
    return services.create_chat(director)


# --- Every review state ------------------------------------------------------------------------------------------


def test_waiting_and_returned_documents_are_indexed(make_document, director, head) -> None:
    waiting = make_document(docx_file("The waiting design says 40,000 pesos."), approve=False)
    returned = document_services.upload_document(head, waiting.activity, DocumentType.objects.get(name="Request Letter"),
                                                 SimpleUploadedFile("letter.docx", docx_file("Returned letter text.")))
    document_services.return_document(director, returned, "Wrong date")
    for document in (waiting, returned):
        assert indexing.index_document(document.pk)["status"] == "indexed"
    assert chunks(document=waiting) and chunks(document=returned)


def test_every_upload_queues_indexing_on_commit(make_document, monkeypatch, django_capture_on_commit_callbacks) -> None:
    queued = []
    monkeypatch.setattr(document_services, "queue_indexing", lambda pk, mode=indexing.MISSING: queued.append((pk, mode)))
    with django_capture_on_commit_callbacks(execute=True):
        waiting = make_document(docx_file("text"), approve=False)  # needs_review is on in these tests
    assert queued == [(waiting.pk, indexing.MISSING)]


def test_an_approval_after_indexing_does_not_read_the_file_again(make_document, fake_gemini) -> None:
    document = make_document(docx_file("Design text."), approve=False)
    indexing.index_document(document.pk)
    calls = len(fake_gemini.embed_calls)
    assert indexing.index_document(document.pk, mode=indexing.MISSING)["status"] == indexing.ALREADY_INDEXED
    assert len(fake_gemini.embed_calls) == calls


def test_replacing_a_waiting_document_reindexes_it(make_document, director, monkeypatch,
                                                    django_capture_on_commit_callbacks) -> None:
    document = make_document(docx_file("Old text."), approve=False)
    indexing.index_document(document.pk)
    queued = []
    monkeypatch.setattr(document_services, "queue_indexing", lambda pk, mode=indexing.MISSING: queued.append(pk))
    with django_capture_on_commit_callbacks(execute=True):
        document_services.replace_document_file(director, document, SimpleUploadedFile("new.docx", docx_file("New text.")))
    assert not chunks(document=document) and queued == [document.pk]  # the old text is never answered from
    indexing.index_document(document.pk)
    assert chunks(document=document) == ["New text."]


def test_rename_embeds_the_same_text_again_without_reading_the_file(make_document, director, fake_gemini, monkeypatch,
                                                                     django_capture_on_commit_callbacks) -> None:
    fake_gemini.ocr_text = OCR_TEXT
    document = make_document(PNG_BYTES, name="letter.png", type_name="Request Letter")
    indexing.index_document(document.pk)
    assert len(fake_gemini.read_calls) == 1
    queued = []
    monkeypatch.setattr(document_services, "queue_indexing", lambda pk, mode=indexing.MISSING: queued.append(mode))
    with django_capture_on_commit_callbacks(execute=True):
        document_services.rename_document(director, document, "Signed request letter")
    assert queued == [indexing.RETITLE]
    result = indexing.index_document(document.pk, mode=indexing.RETITLE)
    assert result["status"] == "indexed" and len(fake_gemini.read_calls) == 1  # no second OCR
    assert fake_gemini.embed_calls[-1]["title"] == "Signed request letter" and chunks(document=document) == [OCR_TEXT]


def test_certificate_evidence_is_never_indexed(make_document, activity, certificate_for) -> None:
    document = make_document(docx_file("evidence"), approve=False)
    Document.objects.filter(pk=document.pk).update(related_certificate=certificate_for(activity))
    assert indexing.index_document(document.pk)["reason"] == "Certificate files are not indexed."


# --- Reading scans with Gemini (OCR) -------------------------------------------------------------------------------


def test_an_image_of_a_letter_is_read_with_ocr(make_document, fake_gemini, caplog) -> None:
    fake_gemini.ocr_text = OCR_TEXT
    document = make_document(PNG_BYTES, name="letter.png", type_name="Request Letter")
    with caplog.at_level(logging.INFO, logger="assistant.indexing"):
        assert indexing.index_document(document.pk)["status"] == "indexed"
    assert chunks(document=document) == [OCR_TEXT]
    assert fake_gemini.read_calls == [{"bytes": len(PNG_BYTES), "mime_type": "image/png", "model": "gemini-3.5-flash-lite"}]
    assert "Ana Cruz" not in caplog.text and "read with AI (OCR)" in caplog.text  # never the text in the logs


def test_a_scanned_pdf_is_read_with_ocr(make_document, fake_gemini) -> None:
    fake_gemini.ocr_text = "Request for the use of the CSU Hall."
    document = make_document(scanned_pdf(), name="scan.pdf", type_name="Request Letter")
    assert indexing.index_document(document.pk)["status"] == "indexed"
    assert fake_gemini.read_calls[0]["mime_type"] == "application/pdf"


def test_a_pdf_with_text_is_never_sent_to_ocr(make_document, fake_gemini) -> None:
    document = make_document(text_pdf("Activity Design", "Budget: 30,000 pesos"), name="design.pdf")
    assert indexing.index_document(document.pk)["status"] == "indexed" and not fake_gemini.read_calls


def test_event_photos_are_never_read(make_document, fake_gemini) -> None:
    fake_gemini.ocr_text = "should never be asked"
    photo = make_document(PNG_BYTES, name="photo.png", type_name="Event Photos")
    result = indexing.index_document(photo.pk)
    assert result["status"] == "not_indexable" and "pictures, not documents" in result["reason"]
    assert not fake_gemini.read_calls


@pytest.mark.parametrize("type_name", ["Attendance Sheet", "Confirmation of Attendance"])
def test_a_scanned_attendance_form_needs_the_tick(make_document, fake_gemini, director, type_name) -> None:
    fake_gemini.ocr_text = OCR_TEXT
    scan = make_document(scanned_pdf(), name="sheet.pdf", type_name=type_name)
    result = indexing.index_document(scan.pk)
    assert result["reason"] == indexing.SCAN_TICK_NEEDED and not fake_gemini.read_calls  # never sent before the tick
    document_services.allow_ai_scan(director, Document.objects.get(pk=scan.pk))
    assert indexing.index_document(scan.pk)["status"] == "indexed" and len(fake_gemini.read_calls) == 1


def test_a_generated_attendance_pdf_with_text_needs_no_tick(make_document, fake_gemini) -> None:
    document = make_document(text_pdf("ATTENDANCE", "1 Ana Cruz Project Officer"), name="att.pdf", type_name="Attendance Sheet")
    assert indexing.index_document(document.pk)["status"] == "indexed" and not fake_gemini.read_calls


def test_the_backfill_never_reads_scans(make_document, fake_gemini) -> None:
    fake_gemini.ocr_text = OCR_TEXT
    scan = make_document(scanned_pdf(), name="scan.pdf", type_name="Request Letter")
    make_document(PNG_BYTES, name="letter.png", type_name="Request Letter")  # images are not in the backfill at all
    assert indexing.index_pending_documents() == {"indexed": 0, "not_indexable": 1, "failed": 0}
    assert not fake_gemini.read_calls and not chunks(document=scan)
    assert indexing.index_document(scan.pk)["status"] == "indexed"  # by hand (index_documents --document)


def test_a_long_scan_is_not_read(make_document, fake_gemini, settings) -> None:
    settings.ASSISTANT_OCR_MAX_PAGES = 0
    scan = make_document(scanned_pdf(), name="scan.pdf", type_name="Request Letter")
    assert "at most 0 are read" in indexing.index_document(scan.pk)["reason"] and not fake_gemini.read_calls


def test_ocr_can_be_turned_off(make_document, fake_gemini, settings) -> None:
    settings.ASSISTANT_OCR_ENABLED = False
    image = make_document(PNG_BYTES, name="letter.png", type_name="Request Letter")
    assert indexing.index_document(image.pk)["reason"] == indexing.OCR_OFF and not fake_gemini.read_calls


def test_an_ocr_failure_keeps_the_old_chunks_and_can_be_retried(make_document, fake_gemini) -> None:
    fake_gemini.ocr_text = OCR_TEXT
    image = make_document(PNG_BYTES, name="letter.png", type_name="Request Letter")
    indexing.index_document(image.pk)
    fake_gemini.ocr_text = AssistantUnavailable("busy", "rate_limit")
    assert indexing.index_document(image.pk)["status"] == "failed" and chunks(document=image) == [OCR_TEXT]


def test_without_the_key_indexing_fails_quietly(make_document, monkeypatch, settings) -> None:
    from assistant import gemini

    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = ""
    document = make_document(docx_file("text"), approve=False)
    result = indexing.index_document(document.pk)
    assert result["status"] == "failed" and "GEMINI_API_KEY" in result["reason"]


def test_signature_images_and_evaluation_photos_are_never_files_to_read(make_document) -> None:
    """They are not DOCUMENT rows (items 434, 435): the only kinds the indexer knows are documents, reports and
    submissions."""
    make_document(docx_file("text"))
    assert {kind for kind, _ in indexing.pending_sources()} <= {indexing.DOCUMENT, indexing.REPORT, indexing.SUBMISSION}
    assert set(indexing.LINK_FIELD) == {indexing.DOCUMENT, indexing.REPORT, indexing.SUBMISSION}


# --- The tick -------------------------------------------------------------------------------------------------------


def test_the_tick_is_stored_logged_and_cleared_by_a_new_file(make_document, director, caplog) -> None:
    scan = make_document(scanned_pdf(), name="sheet.pdf", type_name="Attendance Sheet", approve=False)  # replaceable
    with caplog.at_level(logging.INFO, logger="documents.audit"):
        ticked = document_services.allow_ai_scan(director, scan)
    assert ticked.ai_scan_allowed_by == director and ticked.ai_scan_allowed_at is not None
    assert ActivityAuditLog.objects.filter(activity=scan.activity, new_value__contains="may be read with AI").exists()
    assert f"document_id={scan.pk}" in caplog.text
    with pytest.raises(document_services.DocumentError, match="already"):
        document_services.allow_ai_scan(director, ticked)
    document_services.replace_document_file(director, ticked, SimpleUploadedFile("again.pdf", scanned_pdf()))
    assert Document.objects.get(pk=scan.pk).ai_scan_allowed_at is None


def test_only_scans_of_the_attendance_types_take_the_tick(make_document, director) -> None:
    letter = make_document(scanned_pdf(), name="scan.pdf", type_name="Request Letter")
    sheet_docx = make_document(docx_file("sheet"), name="sheet.docx", type_name="Attendance Sheet")
    for document in (letter, sheet_docx):
        with pytest.raises(document_services.DocumentError, match="Only a scanned"):
            document_services.allow_ai_scan(director, document)


def test_who_may_tick(make_document, view_only) -> None:
    scan = make_document(scanned_pdf(), name="sheet.pdf", type_name="Attendance Sheet")
    with pytest.raises(PermissionDenied):
        document_services.allow_ai_scan(view_only, scan)  # not the uploader, no activity or approval flag


def test_the_tick_needs_who_and_when_together(make_document, director) -> None:
    scan = make_document(scanned_pdf(), name="sheet.pdf", type_name="Attendance Sheet")
    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.filter(pk=scan.pk).update(ai_scan_allowed_by=director)


def test_the_document_page_offers_the_tick(make_document, director, client) -> None:
    scan = make_document(scanned_pdf(), name="sheet.pdf", type_name="Attendance Sheet")
    client.force_login(director)
    html = client.get(reverse("documents:detail", args=[scan.pk])).content.decode()
    assert "Read this scan with AI" in html and reverse("documents:ai_scan", args=[scan.pk]) in html
    response = client.post(reverse("documents:ai_scan", args=[scan.pk]))
    assert response.status_code == 302 and Document.objects.get(pk=scan.pk).ai_scan_allowed_by == director
    html = client.get(reverse("documents:detail", args=[scan.pk])).content.decode()
    assert "allowed the AI assistant to read this scan" in html
    letter = make_document(docx_file("text"), type_name="Request Letter")
    assert "Read this scan with AI" not in client.get(reverse("documents:detail", args=[letter.pk])).content.decode()


# --- Generated reports and submissions ---------------------------------------------------------------------------


@pytest.fixture
def report(director) -> GeneratedReport:
    row = GeneratedReport.objects.create(generated_by=director, report_type="accomplishment",
                                         date_range_start=date(2026, 1, 1), date_range_end=date(2026, 6, 30),
                                         format=GeneratedReport.Format.PDF, file_path="")
    row.file_path.save("report-test.pdf", ContentFile(text_pdf("Accomplishment Report", "12 activities conducted")))
    return row


def test_a_generated_report_is_indexed(report, director) -> None:
    assert indexing.index_source(indexing.REPORT, report.pk)["status"] == "indexed"
    assert "12 activities conducted" in chunks(generated_report=report)[0]
    pending = GeneratedReport.objects.create(generated_by=director, report_type="evaluation",
                                             date_range_start=date(2026, 1, 1), date_range_end=date(2026, 1, 31),
                                             format=GeneratedReport.Format.PDF, file_path="")
    assert indexing.index_source(indexing.REPORT, pending.pk)["reason"] == "The report file is not ready yet."


def test_building_a_report_queues_its_indexing(report, monkeypatch) -> None:
    from reports import services as report_services

    queued = []
    monkeypatch.setattr(indexing, "queue_source_indexing", lambda kind, pk, mode=indexing.MISSING: queued.append((kind, pk)))
    report_services._queue_indexing(report.pk)
    assert queued == [(indexing.REPORT, report.pk)]


@pytest.fixture
def approved_submission(head, director, activity):
    submission = approval_services.submit(head, SimpleUploadedFile("letter.pdf", text_pdf("MOA with ABC Corp", "Term: 2 years")),
                                          "MOA with ABC Corp", "", activity)
    return approval_services.approve_submission(director, submission)


def test_an_approved_submission_is_indexed_and_unapproving_removes_it(approved_submission, director) -> None:
    assert indexing.index_source(indexing.SUBMISSION, approved_submission.pk)["status"] == "indexed"
    assert chunks(submission=approved_submission)
    approval_services.unapprove_submission(director, approved_submission, "Wrong copy")
    assert not chunks(submission=approved_submission)
    assert indexing.index_source(indexing.SUBMISSION, approved_submission.pk)["reason"] == "Only approved submissions are indexed."


def test_a_waiting_submission_is_never_indexed(head, activity, director) -> None:
    waiting = approval_services.submit(head, SimpleUploadedFile("x.pdf", text_pdf("Draft")), "Draft", "", activity)
    assert indexing.index_source(indexing.SUBMISSION, waiting.pk)["status"] == "not_indexable"


def test_a_saved_submission_is_indexed_as_the_document_only(approved_submission, director) -> None:
    indexing.index_source(indexing.SUBMISSION, approved_submission.pk)
    approval_services.save_to_documents(director, approved_submission, DocumentType.objects.get(name="Other"))
    assert not chunks(submission=approved_submission)
    assert indexing.index_source(indexing.SUBMISSION, approved_submission.pk)["status"] == "not_indexable"


def test_submission_excerpts_only_for_members_who_may_open_it(approved_submission, director, head, make_member,
                                                              fake_gemini) -> None:
    fake_gemini.add_topics("moa")
    indexing.index_source(indexing.SUBMISSION, approved_submission.pk)
    outsider = make_member("Admin Staff")
    found = {member.pk: [row.submission_id for row, _ in tools.retrieve(fake_gemini, "MOA", member=member)]
             for member in (director, head, outsider)}
    assert found[director.pk] == [approved_submission.pk]  # a reviewer
    assert found[head.pk] == [approved_submission.pk]  # the submitter
    assert found[outsider.pk] == []  # 404 on its page, so never in an answer
    assert [row.submission_id for row, _ in tools.retrieve(fake_gemini, "MOA")] == []  # no member: none


# --- Answers and source cards ------------------------------------------------------------------------------------


def test_cards_show_the_state_of_every_kind(director, chat, make_document, report, approved_submission, fake_gemini) -> None:
    fake_gemini.add_topics("budget")
    waiting = make_document(docx_file("The budget is 40,000 pesos."), approve=False, title="Design draft")
    report.file_path.save("report-budget.pdf", ContentFile(text_pdf("Budget total 99,000")))
    approved_submission.file_path.save("sub-budget.pdf", ContentFile(text_pdf("Budget of the MOA: 5,000")))
    for kind, pk in ((indexing.DOCUMENT, waiting.pk), (indexing.REPORT, report.pk), (indexing.SUBMISSION, approved_submission.pk)):
        assert indexing.index_source(kind, pk)["status"] == "indexed"
    fake_gemini.replies += [FakeGemini.call(tools.SEARCH_TOOL, query="budget"), FakeGemini.say("Budgets [1][2][3].")]
    answer = services.ask(director, chat, "What does the budget say?")
    excerpts = fake_gemini.tool_results()[0][1]["excerpts"]
    assert 'state="Waiting for review"' in excerpts and 'kind="generated report"' in excerpts
    assert 'state="Approved submission"' in excerpts
    sources = list(AIMessageSource.objects.filter(message=answer.message))
    assert len(sources) == 3 and {bool(s.document_id) + bool(s.generated_report_id) + bool(s.submission_id) for s in sources} == {1}

    view = pages.conversation(chat)[-1]
    cards = {card["badge"]["label"]: card for card in view["file_groups"][0]["cards"]}
    assert set(cards) == {"Waiting for review", "Generated report", "Approved submission"}
    assert cards["Generated report"]["download_url"] == reverse("reports:download", args=[report.pk])
    assert cards["Generated report"]["open_url"] == ""
    assert cards["Approved submission"]["open_url"] == reverse("approvals:detail", args=[approved_submission.pk])
    assert cards["Waiting for review"]["open_url"] == reverse("documents:detail", args=[waiting.pk])


def test_an_approved_copy_card_says_so(director, chat, make_document, fake_gemini) -> None:
    DocumentType.objects.filter(name="Request Letter").update(needs_review=False)
    fake_gemini.add_topics("venue")
    copy = make_document(docx_file("The venue is the CSU Hall."), type_name="Request Letter", approve=False)
    assert document_services.is_approved_copy(Document.objects.get(pk=copy.pk))
    indexing.index_document(copy.pk)
    fake_gemini.replies += [FakeGemini.call(tools.SEARCH_TOOL, query="venue"), FakeGemini.say("The CSU Hall [1].")]
    services.ask(director, chat, "Where is the venue?")
    [card] = pages.conversation(chat)[-1]["file_groups"][0]["cards"]
    assert card["badge"] == {"label": "Approved copy", "tone": "success"}


def test_a_source_needs_exactly_one_file(director, chat, make_document) -> None:
    message = chat.messages.create(role="assistant", content="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        AIMessageSource.objects.create(message=message)
    with pytest.raises(IntegrityError), transaction.atomic():
        DocumentEmbedding.objects.create(chunk_index=0, chunk_text="x", embedding_vector=[0.0] * 768)
