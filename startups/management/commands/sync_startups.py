"""Run the Navigatu startup sync once, by hand. The daily run is done by Django-Q2 (never forced)."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from startups.providers import ProviderNotAvailable
from startups.services import sync_startups


class Command(BaseCommand):
    help = "Sync startups from Navigatu (through the configured StartupProvider). Safe to run again."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Deactivate missing startups even if the safety guard would stop it "
            "(zero records, or more than STARTUP_SYNC_MAX_DEACTIVATE_RATIO of active startups).",
        )

    def handle(self, *args, **options) -> None:
        if not settings.STARTUP_SYNC_ENABLED:  # the hosted review version (ASSUMPTIONS 393): never load startups
            raise CommandError("The startup sync is turned off (STARTUP_SYNC_ENABLED=False). Nothing was changed.")
        try:
            result = sync_startups(force=options["force"])
        except ProviderNotAvailable as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Created: {result['created']}, updated: {result['updated']}, unchanged: {result['unchanged']}, "
                f"deactivated: {result['deactivated']}, skipped: {result['skipped']}."
            )
        )
        if result["deactivation_skipped"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Deactivation skipped (safety guard): {result['deactivation_skipped_reason']} "
                    "Run again with --force to deactivate them on purpose."
                )
            )
