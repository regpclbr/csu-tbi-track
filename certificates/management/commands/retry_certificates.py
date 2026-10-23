"""Run the daily certificate retry once, by hand: render and issue Approved certificates that are
ready, then send Issued participant certificates that were not sent yet. Safe to run any time."""

from django.core.management.base import BaseCommand

from certificates import services


class Command(BaseCommand):
    help = "Render/issue ready certificates and send unsent certificate emails now (the 8:30 daily retry)."

    def handle(self, *args, **options) -> None:
        result = services.retry_pending_certificates()
        self.stdout.write(
            f"PDFs rendered/issued: {result['prepared']} (failed: {result['prepare_failed']}). "
            f"Emails sent: {result['sent']} (failed: {result['send_failed']})."
        )
