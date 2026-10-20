"""Background tasks for startups, run by the Django-Q2 cluster (`python manage.py qcluster`)."""

import logging

from . import services

logger = logging.getLogger("startups.sync")

SYNC_SCHEDULE_NAME = "Daily Navigatu startup sync"
SYNC_TASK = "startups.tasks.run_startup_sync"


def run_startup_sync() -> dict:
    """Scheduled daily at STARTUP_SYNC_HOUR (7:00 AM Manila) by `setup_schedules`.
    Never forced: the deactivation safety guard always applies to scheduled runs."""
    return services.sync_startups(force=False)
