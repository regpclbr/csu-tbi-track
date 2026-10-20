"""Create (or fix) the Django-Q2 schedule for the daily document reminders. Safe to run many times."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django_q.models import Schedule

from documents.tasks import REMINDER_SCHEDULE_NAME, REMINDER_TASK, next_reminder_run


class Command(BaseCommand):
    help = "Create the daily 8:00 AM (Manila) document reminder schedule. Running it again never adds a second one."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        schedules = list(Schedule.objects.select_for_update().filter(name=REMINDER_SCHEDULE_NAME).order_by("pk"))
        for duplicate in schedules[1:]:  # only rows with our own name; normally there are none
            duplicate.delete()
            self.stdout.write(self.style.WARNING(f"Removed a duplicate schedule (id {duplicate.pk})."))

        schedule = schedules[0] if schedules else Schedule(name=REMINDER_SCHEDULE_NAME)
        created = schedule.pk is None
        schedule.func = REMINDER_TASK
        schedule.schedule_type = Schedule.DAILY  # Manila has no daylight saving time, so it stays at 8:00
        schedule.repeats = -1  # forever
        local_next = timezone.localtime(schedule.next_run) if schedule.next_run else None
        if local_next is None or local_next.hour != settings.DOCUMENT_REMINDER_HOUR or local_next.minute != 0:
            schedule.next_run = next_reminder_run()
        schedule.save()

        when = timezone.localtime(schedule.next_run).strftime("%Y-%m-%d %I:%M %p")
        verb = "Created" if created else "Already set up:"
        self.stdout.write(self.style.SUCCESS(f'{verb} "{REMINDER_SCHEDULE_NAME}" - next run {when} (Manila time).'))
