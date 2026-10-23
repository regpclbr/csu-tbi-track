"""Background task for reports, run by the Django-Q2 cluster (`python manage.py qcluster`)."""

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
