"""Indexing approved activity documents: parse → chunk → embed → DOCUMENT_EMBEDDING rows.

Rules (checked in one place, `why_not_indexable`):
- only Approved activity documents (never certificate files, never For Review);
- never the types in settings.ASSISTANT_EXCLUDED_DOCUMENT_TYPES (personal data, e.g. Attendance Sheet);
- only pdf, docx and xlsx with extractable text (images and scanned PDFs need OCR: a TBI question).

Re-indexing a document replaces its rows in one transaction: never duplicates, never half-indexed.
Never log document text: only IDs, counts and reasons.
"""

import io
import logging
import re

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.db.models.functions import Lower, Trim

from documents.models import Document, DocumentStatus

from .gemini import DOCUMENT_TASK, AssistantUnavailable, GeminiGateway, get_gateway
from .models import DocumentEmbedding

logger = logging.getLogger("assistant.indexing")

INDEXED, NOT_INDEXABLE, FAILED = "indexed", "not_indexable", "failed"
PARSED_EXTENSIONS = ("pdf", "docx", "xlsx")
IMAGE_EXTENSIONS = ("jpg", "png")


class NotIndexable(Exception):
    """This document cannot be indexed. The message is the reason (safe to show)."""


# --- Rules ------------------------------------------------------------------------------------


def excluded_type_names() -> set[str]:
    return {name.strip().lower() for name in settings.ASSISTANT_EXCLUDED_DOCUMENT_TYPES}


def why_not_indexable(document: Document) -> str | None:
    """The rule that keeps this document out of the index, or None if it may be indexed."""
    if document.related_certificate_id:
        return "Certificate files are not indexed."
    if document.status.status_name != DocumentStatus.APPROVED:
        return "Only approved documents are indexed."
    if document.document_type.name.strip().lower() in excluded_type_names():
        return f"{document.document_type.name} documents are not indexed (they contain personal data)."
    return None


def with_searchable(queryset: QuerySet) -> QuerySet:
    """Annotate `searchable`: True when the document has embeddings (no column is stored)."""
    return queryset.annotate(searchable=Exists(DocumentEmbedding.objects.filter(document=OuterRef("pk"))))


def pending_documents() -> QuerySet:
    """Approved activity documents of a parsable type and not excluded, with no embeddings yet."""
    return (
        with_searchable(Document.objects.activity_documents().approved())
        .filter(file_extension__in=PARSED_EXTENSIONS, searchable=False)
        .annotate(type_key=Lower(Trim("document_type__name")))
        .exclude(type_key__in=excluded_type_names())
        .order_by("pk")
    )


# --- Parsing ------------------------------------------------------------------------------------


def _pdf_text(stream: io.BytesIO) -> str:
    from pypdf import PdfReader

    reader = PdfReader(stream)
    if reader.is_encrypted and not reader.decrypt(""):
        raise NotIndexable("The PDF is password-protected.")
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(stream: io.BytesIO) -> str:
    from docx import Document as DocxDocument
    from docx.table import Table

    lines = []
    for block in DocxDocument(stream).iter_inner_content():  # paragraphs and tables in reading order
        if isinstance(block, Table):
            for row in block.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
            lines.append("")
        else:
            lines.append(block.text)
    return "\n".join(lines)


def _xlsx_text(stream: io.BytesIO) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(stream, read_only=True, data_only=True)  # data_only: formula results
    lines = []
    for sheet in workbook.worksheets:
        lines.append(f"Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if value is None else str(value).strip() for value in row]
            if any(cells):
                lines.append(" | ".join(cells).strip(" |"))
        lines.append("")
    workbook.close()
    return "\n".join(lines)


PARSERS = {"pdf": _pdf_text, "docx": _docx_text, "xlsx": _xlsx_text}


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(document: Document) -> str:
    """The document's text, or NotIndexable with the reason."""
    extension = document.file_extension.lower()
    if extension in IMAGE_EXTENSIONS:
        raise NotIndexable("Images are not indexed (there is no OCR).")
    if extension not in PARSERS:
        raise NotIndexable(f"{extension.upper() or 'This'} files are not indexed.")
    try:
        with document.file_path.open("rb") as handle:
            data = handle.read()
    except (FileNotFoundError, ValueError, OSError) as error:
        raise NotIndexable("The file is missing on disk.") from error
    try:
        text = PARSERS[extension](io.BytesIO(data))
    except NotIndexable:
        raise
    except Exception as error:  # any parser failure on a damaged or unusual file
        raise NotIndexable(f"The {extension.upper()} file could not be read ({type(error).__name__}).") from error
    text = clean_text(text)
    if not text:
        if extension == "pdf":
            raise NotIndexable("The PDF has no extractable text (probably a scan; OCR is not available).")
        raise NotIndexable(f"The {extension.upper()} file has no text.")
    return text


# --- Chunking ------------------------------------------------------------------------------------

BOUNDARIES = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")


def split_into_chunks(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    """Chunks of about `size` characters, overlapping by about `overlap`, in reading order.
    Cuts at a paragraph, then a line, sentence or word boundary when possible."""
    size = size or settings.ASSISTANT_CHUNK_SIZE
    overlap = settings.ASSISTANT_CHUNK_OVERLAP if overlap is None else overlap
    overlap = min(overlap, size // 3)  # always move forward
    text = text.strip()
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            for boundary in BOUNDARIES:
                position = window.rfind(boundary, size // 2)
                if position != -1:
                    end = start + position + len(boundary)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        next_start = end - overlap
        space = text.find(" ", next_start, end)  # begin the overlap at a word
        start = max(space + 1 if space != -1 else next_start, start + 1)
    return chunks


# --- Indexing ------------------------------------------------------------------------------------


def _load(document_id: int, lock: bool = False) -> Document | None:
    queryset = Document.objects.select_related("status", "document_type")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return queryset.filter(pk=document_id).first()


def _result(status: str, document_id: int, chunks: int = 0, reason: str = "") -> dict:
    return {"document_id": document_id, "status": status, "chunks": chunks, "reason": reason}


def index_document(document_id: int, gateway: GeminiGateway | None = None) -> dict:
    """Index (or re-index) one document. Returns {"status": indexed | not_indexable | failed, …}.

    The slow part (parse, embed) happens outside the transaction; the old rows are replaced by
    the new ones inside one transaction, after checking the rules again on the locked row.
    """
    document = _load(document_id)
    if document is None:
        return _result(NOT_INDEXABLE, document_id, reason="The document does not exist.")
    if reason := why_not_indexable(document):
        removed, _ = DocumentEmbedding.objects.filter(document=document).delete()  # never keep rows it must not have
        logger.info("Document %s not indexed: %s (removed %d old chunks).", document_id, reason, removed)
        return _result(NOT_INDEXABLE, document_id, reason=reason)
    try:
        chunks = split_into_chunks(extract_text(document))
    except NotIndexable as error:
        logger.info("Document %s not indexed: %s", document_id, error)
        return _result(NOT_INDEXABLE, document_id, reason=str(error))

    try:
        vectors = (gateway or get_gateway()).embed(chunks, task_type=DOCUMENT_TASK, title=document.title)
    except AssistantUnavailable as error:
        logger.error("Document %s indexing failed (%s); it can be retried. Old chunks kept.", document_id, error.kind)
        return _result(FAILED, document_id, reason=str(error))
    if len(vectors) != len(chunks) or any(len(v) != settings.EMBEDDING_DIMENSIONS for v in vectors):
        logger.error("Document %s indexing failed: unexpected embedding size.", document_id)
        return _result(FAILED, document_id, reason="The embedding service returned unexpected data.")

    with transaction.atomic():
        locked = _load(document_id, lock=True)
        if locked is None or (reason := why_not_indexable(locked)):
            return _result(NOT_INDEXABLE, document_id, reason=reason or "The document does not exist.")
        DocumentEmbedding.objects.filter(document=locked).delete()
        DocumentEmbedding.objects.bulk_create(
            DocumentEmbedding(document=locked, chunk_index=index, chunk_text=chunk, embedding_vector=vector)
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        )
    logger.info("Document %s indexed: %d chunks.", document_id, len(chunks))
    return _result(INDEXED, document_id, chunks=len(chunks))


def index_pending_documents(gateway: GeminiGateway | None = None) -> dict:
    """Backfill: index every approved document that has no embeddings yet. Safe to run again."""
    counts = {INDEXED: 0, NOT_INDEXABLE: 0, FAILED: 0}
    for document_id in list(pending_documents().values_list("pk", flat=True)):
        counts[index_document(document_id, gateway)["status"]] += 1
    logger.info("Index pending documents: %s", counts)
    return counts


def queue_indexing(document_id: int) -> None:
    """Queue a background indexing task (Django-Q2). Called after an approval is committed."""
    from django_q.tasks import async_task

    async_task("assistant.tasks.run_index_document", document_id, task_name=f"Index document {document_id}")
