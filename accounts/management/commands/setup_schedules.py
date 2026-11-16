"""Create (or fix) every Django-Q2 schedule listed in config/schedules.py. Safe to run many times."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django_q.models import Schedule

from config.schedules import SCHEDULES, DailySchedule, MinutesSchedule, next_daily_run


class Command(BaseCommand):
    help = (
        "Create the schedules (startup sync, document indexing, reminders, certificate retry, activity statuses and "
        "evaluation links every few minutes). Running it again never adds a second one."
    )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        for planned in SCHEDULES:
            self.setup(planned)

    def setup(self, planned: DailySchedule | MinutesSchedule) -> None:
        if not planned.enabled:  # turned off by its setting (e.g. STARTUP_SYNC_ENABLED=False): no schedule row at all
            removed, _ = Schedule.objects.filter(name=planned.name).delete()
            note = " (removed the existing schedule)" if removed else ""
            self.stdout.write(f'Turned off: "{planned.name}" ({planned.enabled_setting}=False){note}.')
            return
        schedules = list(Schedule.objects.select_for_update().filter(name=planned.name).order_by("pk"))
        for duplicate in schedules[1:]:  # only rows with our own name; normally there are none
            duplicate.delete()
            self.stdout.write(self.style.WARNING(f"Removed a duplicate schedule (id {duplicate.pk})."))

        schedule = schedules[0] if schedules else Schedule(name=planned.name)
        created = schedule.pk is None
        schedule.func = planned.func
        schedule.repeats = -1  # forever
        if isinstance(planned, MinutesSchedule):
            schedule.schedule_type = Schedule.MINUTES
            schedule.minutes = planned.minutes
            if schedule.next_run is None or created:
                schedule.next_run = timezone.now() + timedelta(minutes=1)
        else:
            schedule.schedule_type = Schedule.DAILY  # Manila has no daylight saving time, so the hour stays put
            local_next = timezone.localtime(schedule.next_run) if schedule.next_run else None
            if local_next is None or local_next.hour != planned.hour or local_next.minute != planned.minute:
                schedule.next_run = next_daily_run(planned.hour, minute=planned.minute)
        schedule.save()

        when = timezone.localtime(schedule.next_run).strftime("%Y-%m-%d %I:%M %p")
        verb = "Created" if created else "Already set up:"
        repeat = f" ({planned.when.lower()})" if isinstance(planned, MinutesSchedule) else ""
        self.stdout.write(self.style.SUCCESS(f'{verb} "{planned.name}"{repeat} - next run {when} (Manila time).'))
