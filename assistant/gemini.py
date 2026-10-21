"""The ONE place the system talks to Gemini (google-genai).

Everything else uses `get_gateway()` and the small neutral types below, so tests can inject a
fake gateway in one place and never reach Google. Never log the API key or any document text:
log only error codes and counts.
"""

import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger("assistant.gemini")

# Embedding task types (gemini-embedding-001).
DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
QUERY_TASK = "RETRIEVAL_QUERY"


class AssistantUnavailable(Exception):
    """Gemini cannot be used right now. The message is safe to show to a member."""

    def __init__(self, message: str, kind: str = "unavailable", status: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.kind = kind  # "not_configured" | "rate_limit" | "rejected" | "model_unavailable" | "unavailable"
        self.status = status  # Google's HTTP status, if there was a response
        self.detail = detail  # Google's own error message (never the key, never document text)


MISSING_KEY = "The AI assistant is not set up: GEMINI_API_KEY is empty in .env."
RATE_LIMITED = "The AI service is busy (rate limit reached). Please try again in a minute."
UNAVAILABLE = "The AI service is not available right now. Please try again later."
KEY_REJECTED = "The Gemini API key was rejected. Check GEMINI_API_KEY in .env."
MODEL_UNAVAILABLE = "The AI model {model} is not available for this API key. Run: python manage.py check_gemini"
DETAIL_MAX_LENGTH = 300


def google_detail(error: Exception) -> str:
    """Google's error status and message, for logs and check_gemini. The key is masked if it ever appears."""
    status, message = getattr(error, "status", None), getattr(error, "message", None)
    detail = f"{status}: {message}" if status or message else type(error).__name__
    key = (settings.GEMINI_API_KEY or "").strip()
    if key:
        detail = detail.replace(key, "***")
    return " ".join(detail.split())[:DETAIL_MAX_LENGTH]


@dataclass(frozen=True)
class ModelInfo:
    name: str  # without the "models/" prefix
    actions: tuple[str, ...]  # e.g. ("generateContent", "countTokens")


# --- Neutral types -----------------------------------------------------------------------------


@dataclass(frozen=True)
class FunctionCall:
    name: str
    args: dict


@dataclass
class ModelReply:
    """What the chat model answered: text and/or function calls. `raw` is the original
    model message, sent back unchanged in the next round (it may carry thought signatures)."""

    text: str = ""
    function_calls: list[FunctionCall] = field(default_factory=list)
    raw: Any = None


@dataclass
class Turn:
    """One message of the conversation sent to the model."""

    role: str  # "user" | "model" | "tool"
    text: str = ""
    reply: ModelReply | None = None  # role "model": the model's earlier reply
    tool_results: list[tuple[str, dict]] = field(default_factory=list)  # role "tool": (name, result)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema of the arguments


def normalize(vector: list[float]) -> list[float]:
    """Unit length. gemini-embedding-001 only normalizes its full 3072-dimension output, so a
    768-dimension vector must be normalized for cosine/dot-product comparisons."""
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else list(vector)


# --- Gateway -------------------------------------------------------------------------------------


class GeminiGateway(ABC):
    @abstractmethod
    def embed(self, texts: list[str], *, task_type: str, title: str | None = None, model: str | None = None) -> list[list[float]]:
        """One normalized vector (settings.EMBEDDING_DIMENSIONS long) per text, in order.
        `model` defaults to settings.GEMINI_EMBEDDING_MODEL."""

    @abstractmethod
    def generate(self, *, system_instruction: str, turns: list[Turn], tools: list[ToolSpec], model: str | None = None) -> ModelReply:
        """One chat-model round with function calling (the caller runs the tools).
        `model` defaults to settings.GEMINI_CHAT_MODEL."""

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """The models this API key can use, with their supported actions."""


class GoogleGemini(GeminiGateway):
    """The real gateway. Batches embeddings and retries rate limits / server errors with backoff."""

    def __init__(self, api_key: str = "", client: Any = None, max_attempts: int | None = None) -> None:
        if client is None:
            from google import genai
            from google.genai import types

            # A slow or stuck request must never hang a page or a task (timeout is in milliseconds).
            timeout_ms = int(settings.ASSISTANT_REQUEST_TIMEOUT_SECONDS * 1000)
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
        self.client = client
        self.max_attempts = max_attempts  # None: settings.ASSISTANT_MAX_RETRIES (check_gemini uses 1)

    # -- retries --

    def _call(self, what: str, model_name: str, function, /, *args, **kwargs):
        """Call Google; retry rate limits and server errors. Logs show the model, HTTP status and
        Google's message (google_detail): never the key, never the texts sent."""
        from google.genai import errors

        attempts = max(1, self.max_attempts or settings.ASSISTANT_MAX_RETRIES)
        for attempt in range(1, attempts + 1):
            try:
                return function(*args, **kwargs)
            except errors.ClientError as error:
                status, detail = error.code, google_detail(error)
                if error.code in (401, 403):
                    logger.error("Gemini %s (%s) refused: HTTP %s %s", what, model_name, error.code, detail)
                    raise AssistantUnavailable(KEY_REJECTED, "rejected", status, detail) from None
                if error.code == 404:
                    logger.error("Gemini %s (%s) refused: HTTP 404 %s", what, model_name, detail)
                    raise AssistantUnavailable(MODEL_UNAVAILABLE.format(model=model_name), "model_unavailable", status, detail) from None
                if error.code != 429:
                    logger.error("Gemini %s (%s) refused: HTTP %s %s", what, model_name, error.code, detail)
                    raise AssistantUnavailable(UNAVAILABLE, "rejected", status, detail) from None
                kind, message = "rate_limit", RATE_LIMITED
            except errors.ServerError as error:
                status, detail = error.code, google_detail(error)
                logger.warning("Gemini %s (%s) server error: HTTP %s %s", what, model_name, error.code, detail)
                kind, message = "unavailable", UNAVAILABLE
            except (httpx.HTTPError, OSError, errors.APIError) as error:  # network problems, timeouts, other API errors
                status, detail = getattr(error, "code", None), google_detail(error)
                logger.warning("Gemini %s (%s) failed: %s", what, model_name, detail)
                kind, message = "unavailable", UNAVAILABLE
            if attempt == attempts:
                logger.error("Gemini %s (%s) gave up after %d attempt(s): %s, %s", what, model_name, attempts, kind, detail)
                raise AssistantUnavailable(message, kind, status, detail) from None
            wait = 2 ** attempt  # 2, 4, 8 … seconds
            logger.warning("Gemini %s (%s): %s, retrying in %d s (attempt %d of %d).", what, model_name, kind, wait, attempt, attempts)
            sleep(wait)
        raise AssistantUnavailable(UNAVAILABLE)  # not reached

    # -- embeddings --

    def embed(self, texts: list[str], *, task_type: str, title: str | None = None, model: str | None = None) -> list[list[float]]:
        from google.genai import types

        model = model or settings.GEMINI_EMBEDDING_MODEL
        vectors: list[list[float]] = []
        size = max(1, settings.ASSISTANT_EMBED_BATCH_SIZE)
        for start in range(0, len(texts), size):
            batch = texts[start:start + size]
            config = types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=settings.EMBEDDING_DIMENSIONS,
                title=title if task_type == DOCUMENT_TASK and title else None,
            )
            response = self._call(
                "embedding", model, self.client.models.embed_content, model=model, contents=batch, config=config,
            )
            embeddings = list(response.embeddings or [])
            if len(embeddings) != len(batch):
                logger.error("Gemini embedding returned %d vectors for %d texts.", len(embeddings), len(batch))
                raise AssistantUnavailable(UNAVAILABLE)
            vectors.extend(normalize(list(item.values)) for item in embeddings)
        return vectors

    # -- chat with function calling --

    def generate(self, *, system_instruction: str, turns: list[Turn], tools: list[ToolSpec], model: str | None = None) -> ModelReply:
        from google.genai import types

        model = model or settings.GEMINI_CHAT_MODEL
        contents = []
        for turn in turns:
            if turn.role == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=turn.text)]))
            elif turn.role == "model":
                if turn.reply is not None and turn.reply.raw is not None:
                    contents.append(turn.reply.raw)  # unchanged, with any thought signatures
                else:
                    contents.append(types.Content(role="model", parts=[types.Part(text=turn.text)]))
            else:  # tool results
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(name=name, response=result) for name, result in turn.tool_results],
                    )
                )
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                for t in tools
            ])] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        response = self._call("chat", model, self.client.models.generate_content, model=model, contents=contents, config=config)
        candidates = response.candidates or []
        content = candidates[0].content if candidates else None
        parts = list(content.parts or []) if content else []
        return ModelReply(
            text="".join(part.text for part in parts if part.text and not part.thought).strip(),
            function_calls=[FunctionCall(part.function_call.name, dict(part.function_call.args or {})) for part in parts if part.function_call],
            raw=content,
        )


    # -- models --

    def list_models(self) -> list[ModelInfo]:
        pager = self._call("list models", "-", self.client.models.list, config={"page_size": 100})
        return sorted(
            (ModelInfo((m.name or "").removeprefix("models/"), tuple(m.supported_actions or ())) for m in pager),
            key=lambda info: info.name,
        )


def sleep(seconds: float) -> None:
    """Wait between retries (tests replace it so they do not really wait)."""
    time.sleep(seconds)


# The single injection point. Tests set `_override` to a fake (see the root conftest.py).
_override: GeminiGateway | None = None


def get_gateway(max_attempts: int | None = None) -> GeminiGateway:
    """The gateway to use: the test fake if one is injected, else the real Google client.
    `max_attempts=1` turns retries off (check_gemini wants each call's own answer)."""
    if _override is not None:
        return _override
    if not settings.GEMINI_API_KEY.strip():
        raise AssistantUnavailable(MISSING_KEY, "not_configured")
    return GoogleGemini(api_key=settings.GEMINI_API_KEY.strip(), max_attempts=max_attempts)
