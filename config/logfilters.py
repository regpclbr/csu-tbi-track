"""Logging filter that keeps personal tokens out of the logs (ASSUMPTIONS 391).

Personal links carry a secret token in the URL: the evaluation page (/evaluate/<token>/), the startup set-password page
(/startups/set-password/<token>/), the JSON versions under /api/ and Django's password reset
(/accounts/password/reset/<uidb64>/<token>/). Django logs the path of every refused request ("Not Found: /evaluate/…"),
and on the server the logs are kept by Docker, so whoever can read them could open someone's link. The filter replaces
each token with "<hidden>" in the final message; nothing else changes.
"""

import logging
import re

HIDDEN = "<hidden>"

# The word before the token, then the token itself (anything up to the next "/", space, "?" or quote).
_TOKEN_PATHS = re.compile(
    r"(?P<prefix>/(?:evaluate|set-password)/)(?P<token>[^/\s?\"']+)"
    r"|(?P<reset>/password/reset/)(?P<uid>[^/\s?\"']+)/(?P<rtoken>[^/\s?\"']+)"
)


def hide_tokens(text: str) -> str:
    """The text with every token of a personal link replaced by HIDDEN ("done" pages keep their name)."""

    def replace(match: re.Match) -> str:
        if match.group("prefix"):
            token = match.group("token")
            return match.group("prefix") + (token if token in {"done"} else HIDDEN)
        if match.group("uid") == "done":
            return match.group(0)
        return f"{match.group('reset')}{HIDDEN}/{HIDDEN}"

    return _TOKEN_PATHS.sub(replace, text)


class HideTokensFilter(logging.Filter):
    """Rewrites the record's message (with its arguments filled in) without the tokens. Never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a broken format string: leave it to the handler, as without the filter
            return True
        cleaned = hide_tokens(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True
