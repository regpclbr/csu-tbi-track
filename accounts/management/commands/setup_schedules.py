"""Create (or fix) every Django-Q2 schedule listed in config/schedules.py. Safe to run many times."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django_q.models import Schedule

from config.schedules import SCHEDULES, DailySchedule, next_daily_run


class Command(BaseCommand):
    help = "Create the daily schedules (startup sync, document indexing, reminders). Running it again never adds a second one."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        for daily in SCHEDULES:
            self.setup(daily)

    def setup(self, daily: DailySchedule) -> None:
        schedules = list(Schedule.objects.select_for_update().filter(name=daily.name).order_by("pk"))
        for duplicate in schedules[1:]:  # only rows with our own name; normally there are none
            duplicate.delete()
            self.stdout.write(self.style.WARNING(f"Removed a duplicate schedule (id {duplicate.pk})."))

        schedule = schedules[0] if schedules else Schedule(name=daily.name)
        created = schedule.pk is None
        schedule.func = daily.func
        schedule.schedule_type = Schedule.DAILY  # Manila has no daylight saving time, so the hour stays put
        schedule.repeats = -1  # forever
        local_next = timezone.localtime(schedule.next_run) if schedule.next_run else None
        if local_next is None or local_next.hour != daily.hour or local_next.minute != daily.minute:
            schedule.next_run = next_daily_run(daily.hour, minute=daily.minute)
        schedule.save()

        when = timezone.localtime(schedule.next_run).strftime("%Y-%m-%d %I:%M %p")
        verb = "Created" if created else "Already set up:"
        self.stdout.write(self.style.SUCCESS(f'{verb} "{daily.name}" - next run {when} (Manila time).'))
