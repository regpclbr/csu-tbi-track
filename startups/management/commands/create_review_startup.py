"""Create one TEST startup for the hosted review version and email it the "set your password" link (ASSUMPTIONS 396).

    docker compose ... exec web python manage.py create_review_startup --email you@example.com [--name "TEST Startup"]

Use it while the Navigatu sync is off (STARTUP_SYNC_ENABLED=False). The link is never printed: it is only in the email.
"""

from django.core.management.base import BaseCommand, CommandError

from startups.services import REVIEW_STARTUP_DEFAULT_NAME, StartupError, create_review_startup


class Command(BaseCommand):
    help = "Create one TEST startup (no Navigatu ID) and email it the set-password link."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--email", required=True, help="Where the set-password link is sent (also the login email).")
        parser.add_argument("--name", default=REVIEW_STARTUP_DEFAULT_NAME, help='Shown name (always starts with "TEST").')

    def handle(self, *args, **options) -> None:
        try:
            startup = create_review_startup(email=options["email"], name=options["name"])
        except StartupError as error:
            raise CommandError(f"{error} Nothing was created.") from None
        except Exception as error:  # the email could not be sent: nothing was saved (one transaction)
            raise CommandError(f"The set-password email could not be sent ({type(error).__name__}); nothing was created. "
                               "Check the email settings with: python manage.py send_test_email <address>") from None
        self.stdout.write(self.style.SUCCESS(
            f'Created "{startup.name}" <{startup.email}> and emailed its set-password link. '
            "It signs in at the site's /startups/portal/ page."
        ))
