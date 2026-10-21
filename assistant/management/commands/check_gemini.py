"""Check the Gemini setup with the same settings and gateway the app uses. Never prints the API key.

Shows which model names the app uses and where they come from (.env, the terminal's environment,
or the default in settings), lists the models the key can use, then makes one tiny embed call and
one tiny generate call without and with the assistant's tool declarations (one attempt each).
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from dotenv import dotenv_values

from assistant import gemini, tools
from assistant.gemini import DOCUMENT_TASK, AssistantUnavailable, Turn

SETTINGS_DEFAULT = "default in config/settings.py"
PROMPT = "Reply with the single word OK."


class Command(BaseCommand):
    help = "Check Gemini: model names in use, models the key can use, and one tiny embed and generate call."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--chat-model", help="Try this chat model instead of GEMINI_CHAT_MODEL (settings are not changed).")

    def handle(self, *args, **options) -> None:
        chat_model = options["chat_model"] or settings.GEMINI_CHAT_MODEL
        embedding_model = settings.GEMINI_EMBEDDING_MODEL
        self.failures = 0

        self.stdout.write("Gemini settings used by the app")
        self.stdout.write(f"  Chat model:      {chat_model}  ({self.source('GEMINI_CHAT_MODEL', options['chat_model'])})")
        self.stdout.write(f"  Embedding model: {embedding_model}  ({self.source('GEMINI_EMBEDDING_MODEL')})")
        self.stdout.write(f"  Dimensions:      {settings.EMBEDDING_DIMENSIONS}")
        key_set = bool(settings.GEMINI_API_KEY.strip())
        self.stdout.write(f"  API key:         {'set' if key_set else 'NOT SET'} (the value is never shown; {self.source('GEMINI_API_KEY', secret=True)})")
        if not key_set:
            raise CommandError(gemini.MISSING_KEY)

        gateway = gemini.get_gateway(max_attempts=1)  # one attempt: show each call's own answer

        self.stdout.write("\nModels this key can use")
        try:
            models = gateway.list_models()
        except AssistantUnavailable as error:
            self.failures += 1
            self.stdout.write(self.style.ERROR(f"  list models: HTTP {error.status or '-'}  {error.detail or error}"))
            models = []
        for action in ("generateContent", "embedContent"):
            names = [m.name for m in models if action in m.actions]
            self.stdout.write(f"  {action} ({len(names)}): {', '.join(names) if names else '-'}")
        if models:
            self.listed(chat_model, "generateContent", models)
            self.listed(embedding_model, "embedContent", models)

        self.stdout.write("\nTiny calls (one attempt each, no retries)")
        self.try_call("embed", embedding_model, lambda: gateway.embed(["ping"], task_type=DOCUMENT_TASK, model=embedding_model),
                   lambda vectors: f"{len(vectors[0])} numbers")
        self.try_call("generate", chat_model,
                   lambda: gateway.generate(system_instruction=PROMPT, turns=[Turn("user", text=PROMPT)], tools=[], model=chat_model),
                   self.describe_reply)
        self.try_call("generate + tools", chat_model,
                   lambda: gateway.generate(system_instruction=PROMPT, turns=[Turn("user", text=PROMPT)], tools=tools.TOOL_SPECS, model=chat_model),
                   self.describe_reply)

        if self.failures:
            raise CommandError(f"{self.failures} check(s) failed. See the HTTP status and Google's message above.")
        self.stdout.write(self.style.SUCCESS("\nAll checks passed."))

    # -- helpers --

    @staticmethod
    def source(name: str, override: str | None = None, secret: bool = False) -> str:
        """Where a setting's value comes from. A variable already set in the terminal wins over .env."""
        if override:
            return "from --chat-model"
        in_file = dotenv_values(settings.BASE_DIR / ".env").get(name)
        in_env = os.environ.get(name)
        if in_env is None:
            return SETTINGS_DEFAULT
        if in_file is None:
            return "from the terminal's environment (not in .env)"
        if in_env != in_file:
            if secret:
                return "WARNING: the terminal's environment overrides a different value in .env"
            return f"WARNING: from the terminal's environment, which overrides .env ({in_file}); open a new terminal"
        return "from .env"

    def listed(self, model: str, action: str, models: list) -> None:
        if any(m.name == model and action in m.actions for m in models):
            self.stdout.write(f"  {model}: listed for {action}")
        else:
            self.stdout.write(self.style.WARNING(f"  {model}: NOT listed for {action}"))

    @staticmethod
    def describe_reply(reply: gemini.ModelReply) -> str:
        if reply.function_calls:
            return "called " + ", ".join(call.name for call in reply.function_calls)
        return f'replied "{reply.text[:40]}"'

    def try_call(self, label: str, model: str, run, describe) -> None:
        try:
            result = run()
        except AssistantUnavailable as error:
            self.failures += 1
            self.stdout.write(self.style.ERROR(f"  {label:<17} {model:<28} HTTP {error.status or '-':<4} {error.detail or error}"))
            return
        self.stdout.write(self.style.SUCCESS(f"  {label:<17} {model:<28} HTTP 200  OK - {describe(result)}"))
