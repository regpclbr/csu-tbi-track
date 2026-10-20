"""Every Django-Q2 schedule of the project, created by `python manage.py setup_schedules`.

Each runs daily at a fixed hour in Manila time (no daylight saving time, so it stays put).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone


@dataclass(frozen=True)
class DailySchedule:
    name: str  # unique: setup_schedules keeps exactly one row per name
    func: str  # dotted path of the task function
    hour_setting: str  # name of the setting holding the hour (0-23, Manila time)

    @property
    def hour(self) -> int:
        return getattr(settings, self.hour_setting)


SCHEDULES = [
    DailySchedule("Daily document reminders", "documents.tasks.run_document_reminders", "DOCUMENT_REMINDER_HOUR"),
    DailySchedule("Daily Navigatu startup sync", "startups.tasks.run_startup_sync", "STARTUP_SYNC_HOUR"),
]


def next_daily_run(hour: int, now: datetime | None = None) -> datetime:
    """The next <hour>:00 in Manila time (today if it has not passed yet, else tomorrow)."""
    local_now = timezone.localtime(now or timezone.now())
    run = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if run <= local_now:
        run += timedelta(days=1)
    return run
