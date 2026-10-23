"""Check that WeasyPrint can render a PDF on this machine (like check_gemini, but for PDFs).

Renders a one-line test PDF in memory (nothing is written to disk) and prints OK, or the exact
error. On Windows it also shows WEASYPRINT_DLL_DIRECTORIES and whether the Pango/GObject DLLs
are there, because a missing or stale value is the usual cause of "cannot load library".
"""

import os
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

TEST_HTML = "<p>CSU-TBI Track PDF check: ñ é ₱</p>"
GOBJECT_DLL = "libgobject-2.0-0.dll"  # the first library WeasyPrint loads on Windows


def render_test_pdf() -> tuple[str, bytes]:
    """Import WeasyPrint and render TEST_HTML. Returns (WeasyPrint version, PDF bytes).

    Imported here, not at the top, so a missing system library becomes a clear message.
    """
    import weasyprint

    return weasyprint.__version__, weasyprint.HTML(string=TEST_HTML).write_pdf()


class Command(BaseCommand):
    help = "Render a one-line test PDF in memory with WeasyPrint and print OK or the exact error."

    def handle(self, *args, **options) -> None:
        if sys.platform == "win32":
            self.show_dll_directories()
        try:
            version, pdf = render_test_pdf()
        except Exception as error:  # any import or render error: show it exactly as raised
            self.stdout.write(self.style.ERROR(f"WeasyPrint failed: {type(error).__name__}: {error}"))
            raise CommandError(
                "WeasyPrint cannot render PDFs. On Windows: install MSYS2 and Pango, set "
                "WEASYPRINT_DLL_DIRECTORIES, then open a NEW terminal (see CLAUDE.md, WeasyPrint setup)."
            ) from None
        if not pdf.startswith(b"%PDF-"):
            raise CommandError("WeasyPrint returned something that is not a PDF.")
        self.stdout.write(self.style.SUCCESS(f"OK: WeasyPrint {version} rendered a test PDF ({len(pdf):,} bytes, in memory)."))

    def show_dll_directories(self) -> None:
        value = os.environ.get("WEASYPRINT_DLL_DIRECTORIES")
        if not value:
            self.stdout.write("WEASYPRINT_DLL_DIRECTORIES: NOT SET in this terminal")
            return
        self.stdout.write(f"WEASYPRINT_DLL_DIRECTORIES: {value}")
        for folder in filter(None, value.split(os.pathsep)):
            found = (Path(folder) / GOBJECT_DLL).is_file()
            self.stdout.write(f"  {folder}: {GOBJECT_DLL} {'found' if found else 'NOT FOUND'}")
