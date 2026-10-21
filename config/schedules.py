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
    minute_setting: str | None = None  # name of the setting holding the minute (default :00)

    @property
    def hour(self) -> int:
        return getattr(settings, self.hour_setting)

    @property
    def minute(self) -> int:
        return getattr(settings, self.minute_setting) if self.minute_setting else 0


SCHEDULES = [
    DailySchedule("Daily document reminders", "documents.tasks.run_document_reminders", "DOCUMENT_REMINDER_HOUR"),
    DailySchedule("Daily Navigatu startup sync", "startups.tasks.run_startup_sync", "STARTUP_SYNC_HOUR"),
    # Retries approved documents that are not indexed yet (e.g. after a Gemini rate limit).
    DailySchedule("Index pending documents", "assistant.tasks.run_index_pending", "ASSISTANT_INDEX_HOUR", "ASSISTANT_INDEX_MINUTE"),
]


def next_daily_run(hour: int, now: datetime | None = None, minute: int = 0) -> datetime:
    """The next <hour>:<minute> in Manila time (today if it has not passed yet, else tomorrow)."""
    local_now = timezone.localtime(now or timezone.now())
    run = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run <= local_now:
        run += timedelta(days=1)
    return run
