"""Background tasks for documents, run by the Django-Q2 cluster (`python manage.py qcluster`)."""

import logging
from datetime import datetime

from django.conf import settings

from config.schedules import next_daily_run

from . import services

logger = logging.getLogger("documents.reminders")

REMINDER_SCHEDULE_NAME = "Daily document reminders"
REMINDER_TASK = "documents.tasks.run_document_reminders"


def run_document_reminders() -> dict[str, int]:
    """Scheduled daily at DOCUMENT_REMINDER_HOUR (8:00 AM Manila time) by `setup_schedules`."""
    counts = services.send_document_reminders()
    logger.info("Document reminders: %s", counts)
    return counts


def next_reminder_run(now: datetime | None = None) -> datetime:
    """The next DOCUMENT_REMINDER_HOUR:00 in Manila time (today if it has not passed yet, else tomorrow)."""
    return next_daily_run(settings.DOCUMENT_REMINDER_HOUR, now)
