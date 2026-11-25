"""Background tasks for the assistant, run by the Django-Q2 cluster (`python manage.py qcluster`).

They never raise: a failure is logged (without document text) and returned as the task result,
so the cluster keeps running and the document can be retried.
"""

import logging

from . import indexing

logger = logging.getLogger("assistant.indexing")

INDEX_SCHEDULE_NAME = "Index pending documents"
INDEX_PENDING_TASK = "assistant.tasks.run_index_pending"


def run_index_document(document_id: int) -> dict:
    """Older queued tasks (before ASSUMPTIONS 438): index one document."""
    try:
        return indexing.index_document(document_id)
    except Exception as error:  # last safety net: log the kind of error, never the text
        logger.error("Indexing document %s crashed: %s.", document_id, type(error).__name__)
        return {"document_id": document_id, "status": indexing.FAILED, "chunks": 0, "reason": type(error).__name__}


def run_index_source(kind: str, pk: int, mode: str = indexing.MISSING) -> dict:
    """Queued when a file is added, replaced, approved, renamed or ticked (ASSUMPTIONS 438)."""
    try:
        return indexing.index_source(kind, pk, mode=mode)
    except Exception as error:  # last safety net: log the kind of error, never the text
        logger.error("Indexing %s %s crashed: %s.", kind, pk, type(error).__name__)
        return {"kind": kind, "id": pk, "status": indexing.FAILED, "chunks": 0, "reason": type(error).__name__}


def run_generate_chat_title(chat_id: int) -> str:
    """Queued after a chat's first answer (services.queue_chat_title): a short generated title. Any failure keeps
    the first question as the title."""
    from . import services

    try:
        return services.generate_chat_title(chat_id)
    except Exception as error:  # last safety net: the kind of error only, never the question or the answer
        logger.error("Generating the title of chat %s crashed: %s.", chat_id, type(error).__name__)
        return services.TITLE_FAILED


def run_index_pending() -> dict:
    """Daily (7:30 AM Manila): index every file that is not indexed yet (no OCR, ASSUMPTIONS 438)."""
    try:
        return indexing.index_pending_documents()
    except Exception as error:
        logger.error("Indexing pending documents crashed: %s.", type(error).__name__)
        return {"status": indexing.FAILED, "reason": type(error).__name__}
