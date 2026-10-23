"""Indexing: only Approved activity documents; parse pdf/docx/xlsx; chunk in order; replace on re-index;
never index certificates, For Review, excluded types, images or scans; backfill; schedule; task safety."""

import logging

import pytest
from django.core.management import call_command
from django.urls import reverse
from django_q.models import Schedule

from assistant import indexing, tasks
from assistant.gemini import AssistantUnavailable
from assistant.models import DocumentEmbedding
from documents import services as document_services
from documents.models import Document

from .conftest import PNG_BYTES, docx_file, scanned_pdf, text_pdf, xlsx_file

pytestmark = pytest.mark.django_db

DESIGN = ("The Pitching Workshop budget is 30,000 pesos for venue and meals.", "Objectives: teach founders to pitch.")


def rows(document: Document) -> list[DocumentEmbedding]:
    return list(DocumentEmbedding.objects.filter(document=document).order_by("chunk_index"))


# --- Parsing and chunking --------------------------------------------------------------------------


def test_parses_docx_paragraphs_and_tables_in_reading_order(make_document) -> None:
    document = make_document(docx_file("Intro paragraph.", table=[["Item", "Cost"], ["Venue", "10,000"]]))
    text = indexing.extract_text(document)
    assert text.index("Intro paragraph.") < text.index("Venue | 10,000")


def test_parses_pdf_text(make_document) -> None:
    document = make_document(text_pdf("Activity Design", "Budget: 30,000 pesos"), name="design.pdf")
    text = indexing.extract_text(document)
    assert "Budget: 30,000 pesos" in text


def test_parses_xlsx_rows(make_document) -> None:
    document = make_document(xlsx_file([["Item", "Cost"], ["Snacks", 5000]]), name="budget.xlsx")
    assert indexing.extract_text(document) == "Sheet: Budget\nItem | Cost\nSnacks | 5000"


def test_chunks_are_in_order_overlap_and_respect_the_size() -> None:
    sentences = [f"Sentence number {n} talks about the workshop plan." for n in range(80)]
    chunks = indexing.split_into_chunks(" ".join(sentences), size=300, overlap=60)
    assert len(chunks) > 5 and all(len(chunk) <= 300 for chunk in chunks)
    positions = [" ".join(sentences).index(chunk[:40]) for chunk in chunks]
    assert positions == sorted(positions)  # reading order
    assert chunks[0][-30:].split()[-1] in chunks[1]  # neighbouring chunks overlap
    assert "Sentence number 79" in chunks[-1]


def test_short_text_is_one_chunk_and_empty_is_none() -> None:
    assert indexing.split_into_chunks("Short text.") == ["Short text."]
    assert indexing.split_into_chunks("   ") == []


# --- What gets indexed ----------------------------------------------------------------------------------


def test_approved_document_is_indexed(make_document, fake_gemini) -> None:
    document = make_document(docx_file(*DESIGN))
    result = indexing.index_document(document.pk)
    assert result["status"] == "indexed" and result["chunks"] == 1
    [row] = rows(document)
    assert row.chunk_index == 0 and "30,000 pesos" in row.chunk_text and len(row.embedding_vector) == 768
    assert fake_gemini.embed_calls[-1] == {
        "count": 1, "task_type": "RETRIEVAL_DOCUMENT", "title": document.title, "model": "gemini-embedding-001"
    }


def test_for_review_document_is_not_indexed(make_document, fake_gemini) -> None:
    document = make_document(docx_file(*DESIGN), approve=False)
    result = indexing.index_document(document.pk)
    assert result["status"] == "not_indexable" and "approved" in result["reason"]
    assert not rows(document) and not fake_gemini.embed_calls


def test_certificate_document_is_not_indexed(make_document, activity, certificate_for) -> None:
    document = make_document(docx_file(*DESIGN))
    Document.objects.filter(pk=document.pk).update(related_certificate=certificate_for(activity))
    assert indexing.index_document(document.pk)["reason"] == "Certificate files are not indexed."


def test_attendance_sheet_is_never_indexed(make_document, fake_gemini) -> None:
    document = make_document(docx_file("Ana Cruz, ana@example.com, 0917…"), type_name="Attendance Sheet")
    result = indexing.index_document(document.pk)
    assert result["status"] == "not_indexable" and "personal data" in result["reason"]
    assert not rows(document) and not fake_gemini.embed_calls
    assert document.pk not in indexing.pending_documents().values_list("pk", flat=True)


def test_excluded_types_come_from_the_setting_ignoring_case(make_document, settings) -> None:
    settings.ASSISTANT_EXCLUDED_DOCUMENT_TYPES = ["  activity design "]
    document = make_document(docx_file(*DESIGN))
    assert indexing.index_document(document.pk)["status"] == "not_indexable"


def test_newly_excluded_type_loses_its_old_chunks(make_document, settings) -> None:
    document = make_document(docx_file(*DESIGN))
    indexing.index_document(document.pk)
    settings.ASSISTANT_EXCLUDED_DOCUMENT_TYPES = ["Activity Design"]
    indexing.index_document(document.pk)
    assert not rows(document)


def test_image_and_scanned_pdf_are_not_searchable(make_document, client, director, caplog) -> None:
    image = make_document(PNG_BYTES, name="poster.png", type_name="Invitation Letter")
    scan = make_document(scanned_pdf(), name="scan.pdf", type_name="Request Letter")
    with caplog.at_level(logging.INFO, logger="assistant.indexing"):
        assert "Images are not indexed" in indexing.index_document(image.pk)["reason"]
        assert "no extractable text" in indexing.index_document(scan.pk)["reason"]
    assert "not indexed" in caplog.text

    client.force_login(director)
    for document in (image, scan):
        detail = client.get(reverse("documents:api_detail", args=[document.pk])).json()["document"]
        assert detail["searchable"] is False


def test_damaged_pdf_is_logged_not_crashing(make_document) -> None:
    document = make_document(b"%PDF-1.4\n% not really a pdf\n%%EOF\n", name="broken.pdf")
    result = indexing.index_document(document.pk)
    assert result["status"] == "not_indexable" and "could not be read" in result["reason"]


def test_searchable_in_detail_and_repository_with_filter(make_document, client, director) -> None:
    indexed = make_document(docx_file(*DESIGN), title="Indexed")
    not_indexed = make_document(docx_file("Other text"), type_name="Request Letter", title="Waiting")
    indexing.index_document(indexed.pk)
    client.force_login(director)
    detail = client.get(reverse("documents:api_detail", args=[indexed.pk])).json()["document"]
    assert detail["searchable"] is True
    listing = {row["title"]: row["searchable"] for row in client.get("/documents/api/").json()["documents"]}
    assert listing == {"Indexed": True, "Waiting": False}
    only = client.get("/documents/api/", {"searchable": "false"}).json()["documents"]
    assert [row["title"] for row in only] == ["Waiting"]
    assert client.get("/documents/api/", {"searchable": "maybe"}).status_code == 400


# --- Re-indexing and failures ----------------------------------------------------------------------


def test_reindexing_replaces_and_never_duplicates(make_document, settings) -> None:
    settings.ASSISTANT_CHUNK_SIZE = 200
    document = make_document(docx_file(*[f"Paragraph {n} about the incubator budget and plan." for n in range(30)]))
    first = indexing.index_document(document.pk)["chunks"]
    ids_before = {row.pk for row in rows(document)}
    second = indexing.index_document(document.pk)["chunks"]
    after = rows(document)
    assert first == second == len(after) > 1
    assert [row.chunk_index for row in after] == list(range(len(after)))
    assert ids_before.isdisjoint(row.pk for row in after)  # replaced, not added


def test_failed_embedding_keeps_the_old_chunks(make_document, fake_gemini, monkeypatch, caplog) -> None:
    document = make_document(docx_file(*DESIGN))
    indexing.index_document(document.pk)
    before = [row.pk for row in rows(document)]

    def busy(*args, **kwargs):
        raise AssistantUnavailable("busy", "rate_limit")

    monkeypatch.setattr(fake_gemini, "embed", busy)
    with caplog.at_level(logging.ERROR, logger="assistant.indexing"):
        result = indexing.index_document(document.pk)
    assert result["status"] == "failed" and [row.pk for row in rows(document)] == before
    assert "rate_limit" in caplog.text and "30,000" not in caplog.text  # never the document text


def test_task_never_raises(monkeypatch) -> None:
    def crash(document_id, gateway=None):
        raise ValueError("boom")

    monkeypatch.setattr(indexing, "index_document", crash)
    assert tasks.run_index_document(7) == {"document_id": 7, "status": "failed", "chunks": 0, "reason": "ValueError"}


def test_approval_queues_indexing_after_commit(make_document, monkeypatch, django_capture_on_commit_callbacks, head) -> None:
    queued = []
    monkeypatch.setattr(document_services, "queue_indexing", queued.append)
    document = make_document(docx_file(*DESIGN), approve=False)
    with django_capture_on_commit_callbacks(execute=True):
        document_services.approve_document(head, document)
    assert queued == [document.pk]


def test_queue_problem_never_breaks_the_approval(make_document, monkeypatch, django_capture_on_commit_callbacks, head) -> None:
    def broken(document_id):
        raise RuntimeError("queue down")

    monkeypatch.setattr(document_services, "queue_indexing", broken)
    document = make_document(docx_file(*DESIGN), approve=False)
    with django_capture_on_commit_callbacks(execute=True):
        document_services.approve_document(head, document)
    document.refresh_from_db()
    assert document.status.status_name == "Approved"


def test_queue_indexing_creates_a_django_q_task(make_document) -> None:
    from django_q.models import OrmQ

    document = make_document(docx_file(*DESIGN))
    indexing.queue_indexing(document.pk)
    assert OrmQ.objects.count() == 1


# --- Backfill command and schedule ----------------------------------------------------------------------


def test_backfill_command_twice(make_document, capsys) -> None:
    good = make_document(docx_file(*DESIGN))
    make_document(PNG_BYTES, name="poster.png", type_name="Invitation Letter")  # never pending: an image
    make_document(docx_file("Ana, ana@example.com"), type_name="Attendance Sheet")  # never pending: excluded
    make_document(docx_file("Not approved yet"), type_name="Request Letter", approve=False)

    call_command("index_documents")
    out = capsys.readouterr().out
    assert "Indexed: 1, not indexable: 0, failed: 0" in out
    assert rows(good)
    call_command("index_documents")
    assert "Nothing to index" in capsys.readouterr().out


def test_backfill_retries_a_failed_document(make_document, fake_gemini, monkeypatch) -> None:
    document = make_document(docx_file(*DESIGN))
    original = fake_gemini.embed
    monkeypatch.setattr(fake_gemini, "embed", lambda *a, **k: (_ for _ in ()).throw(AssistantUnavailable("busy", "rate_limit")))
    assert indexing.index_pending_documents() == {"indexed": 0, "not_indexable": 0, "failed": 1}
    monkeypatch.setattr(fake_gemini, "embed", original)
    assert tasks.run_index_pending() == {"indexed": 1, "not_indexable": 0, "failed": 0}
    assert rows(document)


def test_command_redoes_one_document(make_document, capsys) -> None:
    document = make_document(docx_file(*DESIGN))
    call_command("index_documents", "--document", str(document.pk))
    call_command("index_documents", "--document", str(document.pk))
    assert f"Document {document.pk}: indexed, 1 chunks." in capsys.readouterr().out
    assert len(rows(document)) == 1


def test_setup_schedules_adds_index_pending_at_7_30() -> None:
    from django.utils import timezone

    call_command("setup_schedules")
    call_command("setup_schedules")
    schedule = Schedule.objects.get(name=tasks.INDEX_SCHEDULE_NAME)
    assert Schedule.objects.count() == 4  # plus the certificate retry (Step 9)
    assert schedule.func == tasks.INDEX_PENDING_TASK and schedule.schedule_type == Schedule.DAILY
    local = timezone.localtime(schedule.next_run)
    assert (local.hour, local.minute) == (7, 30)


# --- Admin ------------------------------------------------------------------------------------------------


def test_admin_is_view_only_and_shows_vector_length(make_document, client, admin_member) -> None:
    document = make_document(docx_file(*DESIGN))
    indexing.index_document(document.pk)
    client.force_login(admin_member)
    listing = client.get("/admin/assistant/documentembedding/").content.decode()
    assert "768" in listing
    row = DocumentEmbedding.objects.get()
    page = client.get(f"/admin/assistant/documentembedding/{row.pk}/change/").content.decode()
    assert 'name="_save"' not in page and "Vector length" in page
    for model in ("documentembedding", "aichat", "aichatmessage", "aimessagesource"):
        assert client.get(f"/admin/assistant/{model}/add/").status_code == 403
