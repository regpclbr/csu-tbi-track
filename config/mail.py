"""Development email backend: prints emails in the terminal without wrapping long lines.

Django's console backend builds each email with the standard 78-character line limit, so a
long link (set-password, evaluation) is shown "quoted-printable": split over several lines with
"=" at the end, which cannot be copied. This backend allows lines up to 998 characters (the
RFC 5322 limit), so links appear on one line. Real sending (SMTP, Step 9) is not affected:
mail programs decode wrapped lines automatically.
"""

import email.policy

from django.core.mail.backends.console import EmailBackend as DjangoConsoleBackend

LONG_LINE_POLICY = email.policy.default.clone(max_line_length=998)


class ConsoleEmailBackend(DjangoConsoleBackend):
    def write_message(self, message) -> None:
        msg = message.message(policy=LONG_LINE_POLICY)
        charset = msg.get_charset().get_output_charset() if msg.get_charset() else "utf-8"
        self.stream.write("%s\n" % msg.as_bytes().decode(charset))
        self.stream.write("-" * 79)
        self.stream.write("\n")
