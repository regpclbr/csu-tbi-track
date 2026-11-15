"""check_gemini: shows the model names in use and their source, lists models, makes one tiny embed and
generate call (with and without tools), prints Google's status and message, never the key.
Uses the fake gateway: no real Gemini call."""

import pytest
from django.core.management import CommandError, call_command

from assistant import gemini
from assistant.gemini import AssistantUnavailable, ModelInfo
from assistant.tools import TOOL_SPECS


@pytest.fixture
def key(settings):
    settings.GEMINI_API_KEY = "SECRET-KEY-must-not-print"
    settings.GEMINI_CHAT_MODEL = "gemini-3.5-flash-lite"
    settings.GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"


@pytest.fixture
def dotenv(monkeypatch):
    """dotenv(values) makes check_gemini read these values as the .env file."""

    def use(values: dict) -> None:
        monkeypatch.setattr("assistant.management.commands.check_gemini.dotenv_values", lambda path: dict(values))

    use({})
    return use


def run(capsys, *args) -> str:
    call_command("check_gemini", *args)
    return capsys.readouterr().out


def test_all_checks_pass(key, dotenv, fake_gemini, capsys) -> None:
    fake_gemini.replies += [fake_gemini.say("OK"), fake_gemini.say("OK")]
    out = run(capsys)
    assert "Chat model:      gemini-3.5-flash-lite" in out and "Embedding model: gemini-embedding-001" in out
    assert "embedContent (1): gemini-embedding-001" in out and "generateContent (1): gemini-3.5-flash-lite" in out
    assert "gemini-3.5-flash-lite: listed for generateContent" in out
    assert "embed             gemini-embedding-001         HTTP 200  OK - 768 numbers" in out
    assert 'generate          gemini-3.5-flash-lite        HTTP 200  OK - replied "OK"' in out
    assert "All checks passed." in out
    tools_sent = [len(call["tools"]) for call in fake_gemini.generate_calls]
    assert tools_sent == [0, len(TOOL_SPECS)]  # without, then with the assistant's tool declarations (7, item 381)
    assert fake_gemini.embed_calls[-1]["model"] == "gemini-embedding-001"


def test_the_key_is_never_printed(key, dotenv, fake_gemini, capsys) -> None:
    fake_gemini.replies += [fake_gemini.say("OK"), fake_gemini.say("OK")]
    out = run(capsys)
    assert "SECRET-KEY" not in out and "API key:         set (the value is never shown" in out


def test_failures_show_status_and_googles_message(key, dotenv, fake_gemini, monkeypatch, capsys) -> None:
    def generate(*, system_instruction, turns, tools, model=None):
        raise AssistantUnavailable(
            "x", "model_unavailable", 404, "NOT_FOUND: This model models/gemini-2.5-flash is no longer available to new users."
        )

    monkeypatch.setattr(fake_gemini, "generate", generate)
    with pytest.raises(CommandError, match="2 check"):
        call_command("check_gemini", "--chat-model", "gemini-2.5-flash")
    out = capsys.readouterr().out
    assert "Chat model:      gemini-2.5-flash  (from --chat-model)" in out
    assert "gemini-2.5-flash: NOT listed for generateContent" in out
    assert "HTTP 404  NOT_FOUND: This model models/gemini-2.5-flash is no longer available to new users." in out
    assert "embed             gemini-embedding-001         HTTP 200" in out  # the embedding still worked


def test_tools_only_failure_is_reported_separately(key, dotenv, fake_gemini, monkeypatch, capsys) -> None:
    original = fake_gemini.generate

    def generate(*, system_instruction, turns, tools, model=None):
        if tools:
            raise AssistantUnavailable("x", "rejected", 400, "INVALID_ARGUMENT: function declarations are not supported")
        return original(system_instruction=system_instruction, turns=turns, tools=tools, model=model)

    monkeypatch.setattr(fake_gemini, "generate", generate)
    fake_gemini.replies += [fake_gemini.say("OK")]
    with pytest.raises(CommandError, match="1 check"):
        call_command("check_gemini")
    out = capsys.readouterr().out
    assert 'generate          gemini-3.5-flash-lite        HTTP 200  OK - replied "OK"' in out
    assert "generate + tools  gemini-3.5-flash-lite        HTTP 400  INVALID_ARGUMENT" in out


def test_a_model_that_calls_a_tool_is_shown(key, dotenv, fake_gemini, capsys) -> None:
    fake_gemini.replies += [fake_gemini.say("OK"), fake_gemini.call("find_activities", name="x")]
    assert "OK - called find_activities" in run(capsys)


def test_missing_key_stops_with_a_clear_message(settings, dotenv, capsys) -> None:
    settings.GEMINI_API_KEY = ""
    with pytest.raises(CommandError, match="GEMINI_API_KEY is empty"):
        call_command("check_gemini")
    assert "API key:         NOT SET" in capsys.readouterr().out


def test_terminal_environment_overriding_dotenv_is_flagged(key, dotenv, fake_gemini, monkeypatch, capsys) -> None:
    """The cause of the Step 8 404: a terminal still holding an old GEMINI_CHAT_MODEL wins over .env."""
    monkeypatch.setenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    monkeypatch.setenv("GEMINI_API_KEY", "OLD-KEY-in-terminal")
    dotenv({"GEMINI_CHAT_MODEL": "gemini-3.5-flash-lite", "GEMINI_EMBEDDING_MODEL": "gemini-embedding-001",
            "GEMINI_API_KEY": "NEW-KEY-in-dotenv"})
    fake_gemini.replies += [fake_gemini.say("OK"), fake_gemini.say("OK")]
    out = run(capsys)
    assert "WARNING: from the terminal's environment, which overrides .env (gemini-3.5-flash-lite)" in out
    assert "Embedding model: gemini-embedding-001  (from .env)" in out
    assert "WARNING: the terminal's environment overrides a different value in .env" in out
    assert "OLD-KEY" not in out and "NEW-KEY" not in out


def test_default_source(key, dotenv, fake_gemini, monkeypatch, capsys) -> None:
    monkeypatch.delenv("GEMINI_CHAT_MODEL", raising=False)
    fake_gemini.replies += [fake_gemini.say("OK"), fake_gemini.say("OK")]
    assert "(default in config/settings.py)" in run(capsys)


def test_list_models_failure_is_reported(key, dotenv, fake_gemini, monkeypatch, capsys) -> None:
    def broken():
        raise AssistantUnavailable("x", "rejected", 403, "PERMISSION_DENIED: API key not valid.")

    monkeypatch.setattr(fake_gemini, "list_models", broken)
    fake_gemini.replies += [fake_gemini.say("OK"), fake_gemini.say("OK")]
    with pytest.raises(CommandError, match="1 check"):
        call_command("check_gemini")
    assert "list models: HTTP 403  PERMISSION_DENIED: API key not valid." in capsys.readouterr().out


def test_checks_use_one_attempt(key, dotenv, monkeypatch, capsys) -> None:
    """check_gemini asks the gateway for max_attempts=1 (no retries hide the real answer)."""
    asked = []
    original = gemini.get_gateway

    def spy(max_attempts=None):
        asked.append(max_attempts)
        return original(max_attempts=max_attempts)

    monkeypatch.setattr(gemini, "get_gateway", spy)
    gemini._override.replies += [gemini._override.say("OK"), gemini._override.say("OK")]
    run(capsys)
    assert asked == [1]


def test_listed_models_come_from_the_gateway(key, dotenv, fake_gemini, capsys) -> None:
    fake_gemini.models = [ModelInfo("gemini-embedding-001", ("embedContent",)), ModelInfo("gemini-3.8-flash", ("generateContent",))]
    fake_gemini.replies += [fake_gemini.say("OK"), fake_gemini.say("OK")]
    out = run(capsys)
    assert "generateContent (1): gemini-3.8-flash" in out
    assert "gemini-3.5-flash-lite: NOT listed for generateContent" in out
