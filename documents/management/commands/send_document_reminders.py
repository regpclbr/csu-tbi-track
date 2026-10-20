"""Run the document reminders once, by hand (for testing). The daily run is done by Django-Q2."""

from django.core.management.base import BaseCommand

from documents.services import send_document_reminders


class Command(BaseCommand):
    help = "Create the due-soon and overdue document reminders now. Never creates the same reminder twice."

    def handle(self, *args, **options) -> None:
        counts = send_document_reminders()
        self.stdout.write(
            self.style.SUCCESS(
                f"Due soon: {counts['due_soon']} new, overdue: {counts['overdue']} new, "
                f"already sent before: {counts['already_sent']}, "
                f"skipped (creator deactivated): {counts['skipped_inactive_creator']}."
            )
        )
