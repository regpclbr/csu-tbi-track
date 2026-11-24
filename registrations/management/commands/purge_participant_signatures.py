"""Delete the participant signatures that are due now (ASSUMPTIONS 434): the daily 7:45 job, run once by hand."""

from django.core.management.base import BaseCommand

from registrations.signatures import purge_signatures


class Command(BaseCommand):
    help = ("Delete participant signature images whose activity's forms are kept (Attendance Sheet, Confirmation of "
            "Attendance, Activity Report), or 1 year after the end date.")

    def handle(self, *args, **options):
        counts = purge_signatures()
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {counts['signatures']} signature(s) of {counts['activities']} activit"
            f"{'y' if counts['activities'] == 1 else 'ies'}."))
