"""Background tasks for startups, run by the Django-Q2 cluster (`python manage.py qcluster`)."""

import logging

from django.conf import settings

from . import services

logger = logging.getLogger("startups.sync")

SYNC_SCHEDULE_NAME = "Daily Navigatu startup sync"
SYNC_TASK = "startups.tasks.run_startup_sync"


def run_startup_sync() -> dict:
    """Scheduled daily at STARTUP_SYNC_HOUR (7:00 AM Manila) by `setup_schedules`.
    Never forced: the deactivation safety guard always applies to scheduled runs. Does nothing when
    STARTUP_SYNC_ENABLED is False (the hosted review version, ASSUMPTIONS 393)."""
    if not settings.STARTUP_SYNC_ENABLED:
        return {"skipped": "STARTUP_SYNC_ENABLED is False"}
    return services.sync_startups(force=False)
