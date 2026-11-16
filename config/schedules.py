"""Every Django-Q2 schedule of the project, created by `python manage.py setup_schedules`.

Two kinds: daily at a fixed hour in Manila time (no daylight saving time, so it stays put), and every few
minutes (the automatic activity statuses and evaluation links).
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
    enabled_setting: str | None = None  # name of a True/False setting that can turn the schedule off (default: always on)

    @property
    def enabled(self) -> bool:
        return getattr(settings, self.enabled_setting) if self.enabled_setting else True

    @property
    def hour(self) -> int:
        return getattr(settings, self.hour_setting)

    @property
    def minute(self) -> int:
        return getattr(settings, self.minute_setting) if self.minute_setting else 0

    @property
    def when(self) -> str:
        """For the Admin Console's System status: "8:30"."""
        return f"{self.hour}:{self.minute:02d}"


@dataclass(frozen=True)
class MinutesSchedule:
    """A task that runs every N minutes (Django-Q2 schedule type MINUTES)."""

    name: str
    func: str
    minutes_setting: str  # name of the setting holding N
    enabled = True  # always on

    @property
    def minutes(self) -> int:
        return getattr(settings, self.minutes_setting)

    @property
    def when(self) -> str:
        return f"Every {self.minutes} min"


SCHEDULES: list[DailySchedule | MinutesSchedule] = [
    DailySchedule("Daily document reminders", "documents.tasks.run_document_reminders", "DOCUMENT_REMINDER_HOUR"),
    # Off with STARTUP_SYNC_ENABLED=False (the hosted review version, ASSUMPTIONS 393): setup_schedules then removes it.
    DailySchedule("Daily Navigatu startup sync", "startups.tasks.run_startup_sync", "STARTUP_SYNC_HOUR",
                  enabled_setting="STARTUP_SYNC_ENABLED"),
    # Retries approved documents that are not indexed yet (e.g. after a Gemini rate limit).
    DailySchedule("Index pending documents", "assistant.tasks.run_index_pending", "ASSISTANT_INDEX_HOUR", "ASSISTANT_INDEX_MINUTE"),
    # Renders/issues Approved + evaluated certificates and sends Issued + unsent ones (Step 9).
    DailySchedule("Retry certificate PDFs and emails", "certificates.tasks.run_certificate_retries", "CERTIFICATE_RETRY_HOUR", "CERTIFICATE_RETRY_MINUTE"),
    # Scheduled -> Ongoing -> Conducted on time, then the first evaluation link to each attendee (ASSUMPTIONS 346, 348).
    MinutesSchedule("Activity statuses and evaluation links", "activities.tasks.run_activity_automation", "ACTIVITY_STATUS_INTERVAL_MINUTES"),
]


def next_daily_run(hour: int, now: datetime | None = None, minute: int = 0) -> datetime:
    """The next <hour>:<minute> in Manila time (today if it has not passed yet, else tomorrow)."""
    local_now = timezone.localtime(now or timezone.now())
    run = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run <= local_now:
        run += timedelta(days=1)
    return run
