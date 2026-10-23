"""check_pdf: renders a test PDF in memory and prints OK or the exact error.
The renderer is replaced, so the tests pass without Pango installed."""

import pytest
from django.core.management import CommandError, call_command

COMMAND = "certificates.management.commands.check_pdf"


def run(capsys) -> str:
    call_command("check_pdf")
    return capsys.readouterr().out


def test_prints_ok_when_a_pdf_renders(monkeypatch, capsys) -> None:
    monkeypatch.setattr(f"{COMMAND}.render_test_pdf", lambda: ("70.0", b"%PDF-1.7 test"))
    out = run(capsys)
    assert "OK: WeasyPrint 70.0 rendered a test PDF (13 bytes, in memory)." in out


def test_prints_the_exact_error(monkeypatch, capsys) -> None:
    def broken():
        raise OSError("cannot load library 'libgobject-2.0-0': error 0x7e")

    monkeypatch.setattr(f"{COMMAND}.render_test_pdf", broken)
    with pytest.raises(CommandError, match="WEASYPRINT_DLL_DIRECTORIES"):
        run(capsys)
    assert "WeasyPrint failed: OSError: cannot load library 'libgobject-2.0-0': error 0x7e" in capsys.readouterr().out


def test_refuses_output_that_is_not_a_pdf(monkeypatch, capsys) -> None:
    monkeypatch.setattr(f"{COMMAND}.render_test_pdf", lambda: ("70.0", b"<html>"))
    with pytest.raises(CommandError, match="not a PDF"):
        run(capsys)


def test_shows_the_dll_folder_on_windows(monkeypatch, capsys, tmp_path) -> None:
    (tmp_path / "libgobject-2.0-0.dll").write_bytes(b"")
    monkeypatch.setattr(f"{COMMAND}.sys.platform", "win32")
    monkeypatch.setenv("WEASYPRINT_DLL_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr(f"{COMMAND}.render_test_pdf", lambda: ("70.0", b"%PDF-1.7"))
    out = run(capsys)
    assert f"WEASYPRINT_DLL_DIRECTORIES: {tmp_path}" in out
    assert "libgobject-2.0-0.dll found" in out


def test_says_when_the_dll_folder_is_not_set(monkeypatch, capsys) -> None:
    monkeypatch.setattr(f"{COMMAND}.sys.platform", "win32")
    monkeypatch.delenv("WEASYPRINT_DLL_DIRECTORIES", raising=False)
    monkeypatch.setattr(f"{COMMAND}.render_test_pdf", lambda: ("70.0", b"%PDF-1.7"))
    assert "WEASYPRINT_DLL_DIRECTORIES: NOT SET in this terminal" in run(capsys)
