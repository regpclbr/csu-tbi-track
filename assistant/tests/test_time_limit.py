"""One question has ASSISTANT_QUESTION_TIMEOUT_SECONDS in total (all rounds, embeddings, retries), so
the synchronous assistant fits under a web server's timeout. Past it: a clear 503, nothing saved."""

import json

import pytest
from google.genai import errors

from assistant import gemini, services
from assistant.gemini import AssistantUnavailable, FunctionCall, GoogleGemini, ModelReply
from assistant.models import AIChatMessage

from .test_gateway import FakeClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def clock(monkeypatch):
    """A fake clock for the time limit: clock["now"] += seconds moves time forward."""
    state = {"now": 1000.0}
    monkeypatch.setattr(gemini, "monotonic", lambda: state["now"])
    return state


def test_the_default_limit_is_90_seconds(settings) -> None:
    assert settings.ASSISTANT_QUESTION_TIMEOUT_SECONDS == 90


def test_a_slow_question_is_a_clear_503_and_nothing_is_saved(client, director, fake_gemini, clock, settings) -> None:
    from assistant.models import AIChat

    chat = AIChat.objects.create(member=director, title="New chat")

    def slow_tool_call(**kwargs) -> ModelReply:
        clock["now"] += settings.ASSISTANT_QUESTION_TIMEOUT_SECONDS + 1  # this round took too long
        return ModelReply(function_calls=[FunctionCall("find_activities", {"title": "x"})])

    fake_gemini.replies.append(slow_tool_call)
    fake_gemini.replies.append(ModelReply(text="never reached"))
    client.force_login(director)
    response = client.post(f"/assistant/api/chats/{chat.pk}/ask/", json.dumps({"question": "What is due?"}), content_type="application/json")
    assert response.status_code == 503
    assert response.json() == {"error": "The AI assistant took too long to answer (more than 90 seconds). Please try again in a moment.", "kind": "timeout"}
    assert AIChatMessage.objects.count() == 0  # neither the question nor an answer
    assert len(fake_gemini.generate_calls) == 1  # no second round was started


def test_a_late_final_answer_is_refused_too(director, fake_gemini, clock, settings) -> None:
    from assistant.models import AIChat

    chat = AIChat.objects.create(member=director, title="New chat")

    def slow_answer(**kwargs) -> ModelReply:
        clock["now"] += settings.ASSISTANT_QUESTION_TIMEOUT_SECONDS + 1
        return ModelReply(text="An answer [1].")

    fake_gemini.replies.append(slow_answer)
    with pytest.raises(AssistantUnavailable) as raised:
        services.ask(director, chat, "What is due?")
    assert raised.value.kind == "timeout"


def test_a_fast_question_is_unaffected(director, fake_gemini, clock) -> None:
    from assistant.models import AIChat

    chat = AIChat.objects.create(member=director, title="New chat")
    fake_gemini.replies.append(ModelReply(text="Nothing found."))
    services.ask(director, chat, "Hello?")  # no exception
    assert gemini.seconds_left() is None  # the limit only applies inside ask()


# --- The real gateway under a limit ------------------------------------------------------------------


def server_error() -> errors.ServerError:
    return errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}})


def test_a_retry_wait_past_the_limit_is_not_started(clock, monkeypatch) -> None:
    waits = []

    def wait(seconds: float) -> None:
        waits.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(gemini, "sleep", wait)
    client = FakeClient(embed_results=[server_error(), server_error(), None])
    real_embed = client.models.embed_content

    def slow_embed(**kwargs):
        clock["now"] += 1.5  # each request takes 1.5 s
        return real_embed(**kwargs)

    client.models.embed_content = slow_embed
    # Limit 5 s: request 1 ends at 1.5 s, the 2 s wait fits (ends at 3.5 s), request 2 ends at 5 s,
    # and the next 4 s wait would go past the limit: it is not started.
    with gemini.time_limit(5), pytest.raises(AssistantUnavailable) as raised:
        GoogleGemini(client=client).embed(["a"], task_type=gemini.QUERY_TASK)
    assert raised.value.kind == "timeout"
    assert waits == [2] and len(client.models.embed_requests) == 2


def test_each_request_gets_at_most_the_time_left(clock, settings) -> None:
    settings.ASSISTANT_REQUEST_TIMEOUT_SECONDS = 60
    client = FakeClient()
    with gemini.time_limit(90):
        GoogleGemini(client=client).embed(["a"], task_type=gemini.QUERY_TASK)
        clock["now"] += 80  # 10 seconds left
        GoogleGemini(client=client).embed(["b"], task_type=gemini.QUERY_TASK)
    first, second = (request["config"].http_options.timeout for request in client.models.embed_requests)
    assert (first, second) == (60_000, 10_000)  # milliseconds
    GoogleGemini(client=client).embed(["c"], task_type=gemini.QUERY_TASK)  # outside a limit (indexing)
    assert client.models.embed_requests[-1]["config"].http_options is None  # the client's own timeout


@pytest.fixture
def director(make_member):
    return make_member("Director")
