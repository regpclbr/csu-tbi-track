"""A fake PDF renderer for tests (installed by the root conftest). Never calls WeasyPrint."""

import re


class FakeRenderer:
    """Records every HTML it is given and returns a tiny fake PDF.

    Set `fail` to an exception to make the next renders raise it.
    """

    def __init__(self) -> None:
        self.rendered: list[str] = []
        self.fail: Exception | None = None

    def render(self, html: str) -> bytes:
        if self.fail is not None:
            raise self.fail
        self.rendered.append(html)
        return b"%PDF-1.7\n% fake certificate\n" + html.encode("utf-8")

    @property
    def last(self) -> str:
        return self.rendered[-1]

    def last_text(self) -> str:
        """The visible text of the last render (tags and data: images removed)."""
        body = self.last.split("<body>", 1)[-1]
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
