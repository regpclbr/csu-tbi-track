"""A fake Gemini gateway for tests. Tests never call the real Gemini API (see the root conftest.py).

Embeddings are deterministic "topic" vectors: each keyword in `topics` owns one dimension, so a
question and a chunk that share a keyword are similar (cosine 1.0) and unrelated texts are not (0).
Chat replies are scripted: append ModelReply objects (or functions that build one) to `replies`.
"""

from collections.abc import Callable

from django.conf import settings

from .gemini import FunctionCall, GeminiGateway, ModelInfo, ModelReply, ToolSpec, Turn, normalize

NOISE_DIMENSION = -1  # the last dimension: texts with no known keyword


class FakeGemini(GeminiGateway):
    def __init__(self) -> None:
        self.topics: dict[str, int] = {}
        self.replies: list[ModelReply | Callable[..., ModelReply]] = []
        self.embed_calls: list[dict] = []
        self.generate_calls: list[dict] = []
        self.models: list[ModelInfo] = [
            ModelInfo("gemini-embedding-001", ("embedContent", "countTextTokens")),
            ModelInfo("gemini-3.5-flash-lite", ("generateContent", "countTokens")),
        ]

    def add_topics(self, *keywords: str) -> None:
        for keyword in keywords:
            self.topics.setdefault(keyword.lower(), len(self.topics))

    def vector(self, text: str) -> list[float]:
        vector = [0.0] * settings.EMBEDDING_DIMENSIONS
        lowered = text.lower()
        for keyword, dimension in self.topics.items():
            if keyword in lowered:
                vector[dimension] += 1.0
        if not any(vector):
            vector[NOISE_DIMENSION] = 1.0
        return normalize(vector)

    def embed(self, texts: list[str], *, task_type: str, title: str | None = None, model: str | None = None) -> list[list[float]]:
        self.embed_calls.append({"count": len(texts), "task_type": task_type, "title": title, "model": model or settings.GEMINI_EMBEDDING_MODEL})
        return [self.vector(text) for text in texts]

    def generate(self, *, system_instruction: str, turns: list[Turn], tools: list[ToolSpec], model: str | None = None) -> ModelReply:
        self.generate_calls.append({
            "system_instruction": system_instruction, "turns": list(turns), "tools": list(tools),
            "model": model or settings.GEMINI_CHAT_MODEL,
        })
        if not self.replies:
            raise AssertionError("FakeGemini: no scripted reply left for this generate() call.")
        reply = self.replies.pop(0)
        return reply(system_instruction=system_instruction, turns=turns, tools=tools) if callable(reply) else reply

    def list_models(self) -> list[ModelInfo]:
        return list(self.models)

    # -- helpers for scripting --

    @staticmethod
    def call(tool: str, /, **args) -> ModelReply:
        return ModelReply(function_calls=[FunctionCall(tool, args)])

    @staticmethod
    def say(text: str) -> ModelReply:
        return ModelReply(text=text)

    def tool_results(self, call_number: int = -1) -> list[tuple[str, dict]]:
        """The tool results sent to the model in a given generate() call (default: the last one)."""
        turns = self.generate_calls[call_number]["turns"]
        return [result for turn in turns if turn.role == "tool" for result in turn.tool_results]
