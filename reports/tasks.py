"""Background tasks for reports and the report generator, run by the Django-Q2 cluster (`python manage.py qcluster`)."""

import logging

from . import services

logger = logging.getLogger("reports")


def run_build_report(report_id: int) -> dict:
    """Queued when a member requests a report (task name "report-<id>").

    A failure is logged (report ID and error type only) and raised again, so Django-Q2 records the
    task as failed and the report's state becomes "failed" (Q_CLUSTER["ack_failures"]: not re-queued).
    """
    try:
        return {"report_id": report_id, "result": services.build_report(report_id)}
    except Exception as error:
        logger.error("Generating report %s failed: %s.", report_id, type(error).__name__)
        raise


def run_generate_draft(version_id: int) -> str:
    """Queued for each new report draft version (ASSUMPTIONS 442). Never raises: an unexpected error marks the version
    failed in plain words (the member clicks "Try again"); the log has the IDs and the error type only."""
    from . import generator
    from .models import ReportDraftVersion

    try:
        return generator.generate_version(version_id)
    except Exception as error:
        logger.error("Writing report draft version %s crashed: %s.", version_id, type(error).__name__)
        ReportDraftVersion.objects.filter(pk=version_id).exclude(state=ReportDraftVersion.State.READY).update(
            state=ReportDraftVersion.State.FAILED, progress="", error="Something went wrong while writing this version.")
        return ReportDraftVersion.State.FAILED


def run_read_paper_sheet(read_id: int) -> str:
    """Queued when staff click "Read the rows" (ASSUMPTIONS 443). Never raises (the sheet then says it could not be read)."""
    from . import paper_sheets
    from .models import PaperSheetRead

    try:
        return paper_sheets.read_sheet(read_id)
    except Exception as error:
        logger.error("Reading paper sheet %s crashed: %s.", read_id, type(error).__name__)
        PaperSheetRead.objects.filter(pk=read_id, state=PaperSheetRead.State.READING).update(
            state=PaperSheetRead.State.FAILED, error="Something went wrong while reading this sheet.")
        return PaperSheetRead.State.FAILED
