"""Background tasks for documents, run by the Django-Q2 cluster (`python manage.py qcluster`)."""

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

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
    local_now = timezone.localtime(now or timezone.now())
    run = local_now.replace(hour=settings.DOCUMENT_REMINDER_HOUR, minute=0, second=0, microsecond=0)
    if run <= local_now:
        run += timedelta(days=1)
    return run
