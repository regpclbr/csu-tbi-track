"""Email backends (settings.MAILERS).

ConsoleEmailBackend (development): prints emails in the terminal.
- Long lines are not wrapped. Django's console backend uses the standard 78-character limit, so a
  long link (set-password, evaluation) was shown "quoted-printable": split over several lines with
  "=" at the end, which cannot be copied. Lines may now be up to 998 characters (the RFC 5322 limit).
- Attachments are summarized ("[attachment TBI-2026-00001.pdf, application/pdf, 52,113 bytes]")
  instead of printing thousands of lines of base64.

GuardedSMTPBackend (real sending): Django's SMTP backend, but it never emails an address in
settings.EMAIL_BLOCKED_DOMAINS (the demo data, @demo.example.com), so demo data can never reach a
real mailbox. Those emails are printed to the console while DEBUG is on; with DEBUG off only the
fact that one was blocked is logged (never the body: it may hold a link or a temporary password).
"""

import copy
import email.policy
import logging
import sys
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.console import EmailBackend as DjangoConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as DjangoSMTPBackend

LONG_LINE_POLICY = email.policy.default.clone(max_line_length=998)

logger = logging.getLogger("config.mail")


# Shown instead of "… was emailed" while emails are only printed (ASSUMPTIONS 342).
CONSOLE_MODE_NOTICE = "Email printed in the terminal (development mode). Set EMAIL_REAL_SENDING=True to send real emails."


def prints_emails(alias: str = "default") -> bool:
    """True when the mailer only prints emails in the terminal (the console backend). The one email-mode check,
    also used by the Admin Console's System status."""
    return settings.MAILERS[alias]["BACKEND"] == settings.CONSOLE_MAILER["BACKEND"]


def email_sent_message(sent: str, before: str = "", alias: str = "default") -> str:
    """A success message about an email: `before` + `sent` when emails are really sent; `before` +
    CONSOLE_MODE_NOTICE when they are only printed (e.g. before="Ana was added. ")."""
    return before + (CONSOLE_MODE_NOTICE if prints_emails(alias) else sent)


class ConsoleEmailBackend(DjangoConsoleBackend):
    def write_message(self, message) -> None:
        msg = message.message(policy=LONG_LINE_POLICY)
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                size = len(part.get_payload(decode=True) or b"")
                summary = f"[attachment {part.get_filename()}, {part.get_content_type()}, {size:,} bytes - not printed]"
                del part["Content-Transfer-Encoding"]
                part.set_payload(summary)
        charset = msg.get_charset().get_output_charset() if msg.get_charset() else "utf-8"
        self.stream.write("%s\n" % msg.as_bytes().decode(charset))
        self.stream.write("-" * 79)
        self.stream.write("\n")


def is_blocked_address(address: str) -> bool:
    """True for an address in a blocked domain (or one of its subdomains), ignoring case."""
    domain = parseaddr(address)[1].rpartition("@")[2].lower()
    return any(domain == blocked or domain.endswith("." + blocked) for blocked in settings.EMAIL_BLOCKED_DOMAINS)


def _without_blocked(message):
    """A copy of the message with blocked recipients removed (None if nobody is left)."""
    kept = copy.copy(message)
    kept.to = [a for a in message.to if not is_blocked_address(a)]
    kept.cc = [a for a in message.cc if not is_blocked_address(a)]
    kept.bcc = [a for a in message.bcc if not is_blocked_address(a)]
    return kept if kept.recipients() else None


class GuardedSMTPBackend(DjangoSMTPBackend):
    """Real SMTP, except for blocked demo domains (printed to the console instead)."""

    def send_messages(self, email_messages) -> int:
        real, blocked = [], []
        for message in email_messages:
            if any(is_blocked_address(a) for a in message.recipients()):
                blocked.append(message)
                kept = _without_blocked(message)
                if kept is not None:
                    real.append(kept)
            else:
                real.append(message)
        if blocked and settings.DEBUG:
            logger.info("%d email(s) to a blocked demo domain printed instead of sent.", len(blocked))
            ConsoleEmailBackend(stream=sys.stdout, alias=self.alias).send_messages(blocked)
        elif blocked:
            # Not in DEBUG (a real server): never print the body, which may hold a link or a password.
            logger.info("%d email(s) to a blocked demo domain were not sent.", len(blocked))
        sent = super().send_messages(real) if real else 0
        # A message whose recipients were all blocked counts as handled (printed or skipped on purpose).
        return sent + sum(1 for m in blocked if _without_blocked(m) is None)
