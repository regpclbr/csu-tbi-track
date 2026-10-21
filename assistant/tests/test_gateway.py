"""The real Gemini gateway, driven by a fake google-genai client object (no network, ever)."""

import math

import pytest
from django.conf import settings
from google.genai import errors, types

from assistant import gemini
from assistant.gemini import (
    DOCUMENT_TASK,
    QUERY_TASK,
    AssistantUnavailable,
    FunctionCall,
    GoogleGemini,
    ModelReply,
    ToolSpec,
    Turn,
    normalize,
)


class FakeModels:
    """Stands in for client.models: records requests and returns scripted results or errors."""

    def __init__(self, embed_results=None, generate_results=None):
        self.embed_results = list(embed_results or [])
        self.generate_results = list(generate_results or [])
        self.embed_requests: list[dict] = []
        self.generate_requests: list[dict] = []

    def embed_content(self, *, model, contents, config):
        self.embed_requests.append({"model": model, "contents": list(contents), "config": config})
        result = self.embed_results.pop(0) if self.embed_results else None
        if isinstance(result, Exception):
            raise result
        values = [[3.0, 4.0] + [0.0] * (settings.EMBEDDING_DIMENSIONS - 2) for _ in contents]
        return types.EmbedContentResponse(embeddings=[types.ContentEmbedding(values=v) for v in values])

    def generate_content(self, *, model, contents, config):
        self.generate_requests.append({"model": model, "contents": list(contents), "config": config})
        result = self.generate_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return types.GenerateContentResponse.model_validate(result)


class FakeClient:
    def __init__(self, **kwargs):
        self.models = FakeModels(**kwargs)


def rate_limited() -> errors.ClientError:
    return errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})


def test_normalize_gives_unit_length() -> None:
    vector = normalize([3.0, 4.0])
    assert vector == [0.6, 0.8] and math.isclose(sum(v * v for v in vector), 1.0)
    assert normalize([0.0, 0.0]) == [0.0, 0.0]


def test_embed_uses_task_type_dimensions_batches_and_normalizes(settings) -> None:
    settings.ASSISTANT_EMBED_BATCH_SIZE = 2
    client = FakeClient()
    vectors = GoogleGemini(client=client).embed(["a", "b", "c"], task_type=DOCUMENT_TASK, title="Design")
    requests = client.models.embed_requests
    assert [r["contents"] for r in requests] == [["a", "b"], ["c"]]  # batched
    config = requests[0]["config"]
    assert config.task_type == "RETRIEVAL_DOCUMENT" and config.output_dimensionality == 768 and config.title == "Design"
    assert requests[0]["model"] == settings.GEMINI_EMBEDDING_MODEL
    assert len(vectors) == 3 and len(vectors[0]) == 768
    assert vectors[0][:2] == [0.6, 0.8]  # normalized


def test_query_embedding_has_no_title() -> None:
    client = FakeClient()
    GoogleGemini(client=client).embed(["what is the budget?"], task_type=QUERY_TASK, title="ignored")
    config = client.models.embed_requests[0]["config"]
    assert config.task_type == "RETRIEVAL_QUERY" and config.title is None


def test_rate_limit_is_retried_with_backoff(monkeypatch) -> None:
    waits = []
    monkeypatch.setattr(gemini, "sleep", waits.append)
    client = FakeClient(embed_results=[rate_limited(), rate_limited(), None])
    vectors = GoogleGemini(client=client).embed(["a"], task_type=DOCUMENT_TASK)
    assert len(vectors) == 1 and waits == [2, 4]
    assert len(client.models.embed_requests) == 3


def test_rate_limit_every_time_fails_cleanly(monkeypatch, settings) -> None:
    settings.ASSISTANT_MAX_RETRIES = 3
    waits = []
    monkeypatch.setattr(gemini, "sleep", waits.append)
    client = FakeClient(embed_results=[rate_limited()] * 3)
    with pytest.raises(AssistantUnavailable) as raised:
        GoogleGemini(client=client).embed(["a"], task_type=DOCUMENT_TASK)
    assert raised.value.kind == "rate_limit" and "busy" in str(raised.value)
    assert waits == [2, 4]


def test_server_errors_are_retried() -> None:
    client = FakeClient(embed_results=[errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}}), None])
    assert len(GoogleGemini(client=client).embed(["a"], task_type=DOCUMENT_TASK)) == 1


def test_rejected_key_is_not_retried() -> None:
    client = FakeClient(embed_results=[errors.ClientError(403, {"error": {"code": 403, "status": "PERMISSION_DENIED"}})])
    with pytest.raises(AssistantUnavailable, match="API key was rejected") as raised:
        GoogleGemini(client=client).embed(["a"], task_type=DOCUMENT_TASK)
    assert raised.value.kind == "rejected" and len(client.models.embed_requests) == 1


def test_errors_never_log_the_key_or_text(monkeypatch, settings, caplog) -> None:
    settings.ASSISTANT_MAX_RETRIES = 2
    client = FakeClient(embed_results=[rate_limited(), rate_limited()])
    with pytest.raises(AssistantUnavailable):
        GoogleGemini(api_key="SECRET-KEY-123", client=client).embed(["PRIVATE DOCUMENT TEXT"], task_type=DOCUMENT_TASK)
    assert "SECRET-KEY-123" not in caplog.text and "PRIVATE DOCUMENT TEXT" not in caplog.text


def test_unknown_model_gives_googles_message_in_the_log_and_a_clear_error(caplog, settings) -> None:
    settings.GEMINI_API_KEY = "SECRET-KEY-123"
    message = "This model models/gemini-2.5-flash is no longer available to new users. Key SECRET-KEY-123"
    client = FakeClient(generate_results=[errors.ClientError(404, {"error": {"code": 404, "message": message, "status": "NOT_FOUND"}})])
    with pytest.raises(AssistantUnavailable) as raised:
        GoogleGemini(client=client).generate(system_instruction="S", turns=[Turn("user", text="PRIVATE QUESTION")], tools=[], model="gemini-2.5-flash")
    error = raised.value
    assert error.kind == "model_unavailable" and error.status == 404
    assert str(error) == "The AI model gemini-2.5-flash is not available for this API key. Run: python manage.py check_gemini"
    assert error.detail.startswith("NOT_FOUND: This model models/gemini-2.5-flash is no longer available")
    assert "Gemini chat (gemini-2.5-flash) refused: HTTP 404 NOT_FOUND: This model" in caplog.text
    assert "SECRET-KEY-123" not in caplog.text and "SECRET-KEY-123" not in error.detail  # masked: "***"
    assert "PRIVATE QUESTION" not in caplog.text


def test_long_google_messages_are_cut() -> None:
    client = FakeClient(embed_results=[errors.ClientError(400, {"error": {"code": 400, "message": "x" * 1000, "status": "INVALID_ARGUMENT"}})])
    with pytest.raises(AssistantUnavailable) as raised:
        GoogleGemini(client=client).embed(["a"], task_type=DOCUMENT_TASK)
    assert len(raised.value.detail) == gemini.DETAIL_MAX_LENGTH and raised.value.status == 400


def test_one_attempt_mode_does_not_retry() -> None:
    client = FakeClient(embed_results=[rate_limited(), None])
    with pytest.raises(AssistantUnavailable) as raised:
        GoogleGemini(client=client, max_attempts=1).embed(["a"], task_type=DOCUMENT_TASK)
    assert raised.value.kind == "rate_limit" and raised.value.status == 429 and len(client.models.embed_requests) == 1


def test_the_real_client_gets_a_timeout(monkeypatch, settings) -> None:
    from google import genai

    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(genai, "Client", capture)
    settings.ASSISTANT_REQUEST_TIMEOUT_SECONDS = 45
    GoogleGemini(api_key="k")
    assert captured["http_options"].timeout == 45000  # milliseconds
    assert captured["http_options"].retry_options is None  # the library never retries; we do


def test_list_models(settings) -> None:
    class Models(FakeModels):
        def list(self, *, config):
            return [types.Model(name="models/gemini-3.8-flash", supported_actions=["generateContent"]),
                    types.Model(name="models/gemini-embedding-001", supported_actions=["embedContent"])]

    client = FakeClient()
    client.models = Models()
    infos = GoogleGemini(client=client).list_models()
    assert [(m.name, m.actions) for m in infos] == [("gemini-3.8-flash", ("generateContent",)), ("gemini-embedding-001", ("embedContent",))]


def test_generate_sends_tools_and_reads_function_calls() -> None:
    raw = {"candidates": [{"content": {"role": "model", "parts": [
        {"function_call": {"name": "find_activities", "args": {"name": "Pitch"}}, "thought_signature": "YWJj"},
    ]}}]}
    final = {"candidates": [{"content": {"role": "model", "parts": [{"text": "Done [1]."}]}}]}
    client = FakeClient(generate_results=[raw, final])
    gateway = GoogleGemini(client=client)
    spec = ToolSpec("find_activities", "Find activities.", {"type": "object", "properties": {"name": {"type": "string"}}})

    reply = gateway.generate(system_instruction="SYSTEM", turns=[Turn("user", text="Hi")], tools=[spec])
    assert reply.function_calls == [FunctionCall("find_activities", {"name": "Pitch"})] and reply.text == ""
    config = client.models.generate_requests[0]["config"]
    assert config.system_instruction == "SYSTEM" and config.automatic_function_calling.disable is True
    assert config.tools[0].function_declarations[0].name == "find_activities"

    turns = [Turn("user", text="Hi"), Turn("model", reply=reply), Turn("tool", tool_results=[("find_activities", {"activities": []})])]
    second = gateway.generate(system_instruction="SYSTEM", turns=turns, tools=[spec])
    assert second.text == "Done [1]."
    sent = client.models.generate_requests[1]["contents"]
    assert sent[1] is reply.raw and sent[1].parts[0].thought_signature == b"abc"  # sent back unchanged
    assert sent[2].parts[0].function_response.name == "find_activities"


def test_get_gateway_without_a_key_gives_a_clear_error(monkeypatch, settings) -> None:
    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = "  "
    with pytest.raises(AssistantUnavailable, match="GEMINI_API_KEY is empty") as raised:
        gemini.get_gateway()
    assert raised.value.kind == "not_configured"


def test_a_real_client_can_never_be_created_in_tests(monkeypatch, settings) -> None:
    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = "a-key"
    with pytest.raises(RuntimeError, match="never call the real Gemini API"):
        gemini.get_gateway()


def test_model_reply_defaults() -> None:
    assert ModelReply().text == "" and ModelReply().function_calls == []
