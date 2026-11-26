"""Static checks of project rules from CLAUDE.md, by reading the source files.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def project_files() -> list[Path]:
    """Every project .py file except virtual environments, migrations and tests."""
    return [
        path for path in ROOT.rglob("*.py")
        if not ({".venv", "venv", "migrations", "tests", "__pycache__"} & set(path.relative_to(ROOT).parts))
        and path.name != "conftest.py"
    ]


def files_matching(pattern: str) -> set[str]:
    regex = re.compile(pattern, re.MULTILINE)
    return {path.relative_to(ROOT).as_posix() for path in project_files() if regex.search(path.read_text(encoding="utf-8"))}


def test_the_scan_finds_the_project() -> None:
    names = {path.relative_to(ROOT).as_posix() for path in project_files()}
    assert "config/json.py" in names and "assistant/gemini.py" in names and len(names) > 50


def test_only_config_json_uses_djangos_jsonresponse() -> None:
    """API views return config.json.JsonResponse (Manila time), never Django's own."""
    assert files_matching(r"^\s*from django\.http import[^\n]*\bJsonResponse\b") == {"config/json.py"}
    assert files_matching(r"django\.http\.JsonResponse|http\.JsonResponse\(") == set()


def test_only_gemini_py_imports_google_genai() -> None:
    """Gemini is reached ONLY through assistant/gemini.py."""
    assert files_matching(r"^\s*(from google import genai|from google\.genai\b|import google\.genai\b)") == {"assistant/gemini.py"}
