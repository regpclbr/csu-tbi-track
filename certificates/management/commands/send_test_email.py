"""Send one short test email, to check the email settings. Never prints the SMTP password.

    python manage.py send_test_email you@example.com          # the app's normal mailer
    python manage.py send_test_email you@example.com --smtp   # real SMTP, even while DEBUG prints emails
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from config.mail import email_sent_message, is_blocked_address


class Command(BaseCommand):
    help = "Send one test email to the given address (--smtp: really send it through SMTP)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("address", help="Where to send the test email.")
        parser.add_argument("--smtp", action="store_true", help="Use real SMTP (MAILERS['smtp']) even when DEBUG prints emails.")

    def handle(self, *args, **options) -> None:
        address = options["address"].strip()
        try:
            validate_email(address)
        except ValidationError as error:
            raise CommandError(f"Not a valid email address: {address}") from error

        alias = "smtp" if options["smtp"] else "default"
        config = settings.MAILERS[alias]
        backend = config["BACKEND"]
        self.stdout.write(f"Mailer: {alias}  ({backend})")
        if backend.endswith("SMTPBackend"):
            smtp = config.get("OPTIONS", {})
            self.stdout.write(
                f"  SMTP server: {smtp.get('host')}:{smtp.get('port')}  TLS: {smtp.get('use_tls')}  "
                f"user: {smtp.get('username') or '(none)'}  password: {'set' if smtp.get('password') else 'NOT SET'} (never shown)"
            )
            if is_blocked_address(address):
                self.stdout.write(self.style.WARNING("  This is a blocked demo address: it is printed, not sent."))
        else:
            self.stdout.write("  Development mode: the email is printed below, not sent. Use --smtp to really send it.")
        self.stdout.write(f"  From: {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"  SITE_URL (the address in every email link): {settings.SITE_URL}")

        message = EmailMessage(
            subject="CSU-TBI Track test email",
            body=f"This is a test email from CSU-TBI Track. If you can read it, sending works.\n\nCSU-TBI Track: {settings.SITE_URL}/\n",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[address],
        )
        try:
            message.send(using=alias)
        except Exception as error:  # SMTP errors: show the server's own answer (never the password)
            raise CommandError(f"Sending failed: {type(error).__name__}: {error}") from None
        self.stdout.write(self.style.SUCCESS(email_sent_message(f"Sent to {address}.", alias=alias)))
