"""JSON API for the AI assistant. Any active staff member; chats are private to their member.

Views are thin: parse input, call assistant.services. Gemini problems become a clear 503 message,
never a stack trace.
"""

import json
from collections.abc import Callable
from functools import wraps

from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.http import HttpRequest
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from config.json import JsonResponse  # date-times in Manila time

from accounts.permissions import staff_login_required

from . import services
from .gemini import AssistantUnavailable
from .models import AIChat, AIChatMessage, AIMessageSource


class BadRequest(Exception):
    """Invalid request body."""


def api_errors(view: Callable) -> Callable:
    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> JsonResponse:
        try:
            return view(request, *args, **kwargs)
        except ValidationError as error:
            errors = error.message_dict if hasattr(error, "error_dict") else {"__all__": error.messages}
            return JsonResponse({"errors": errors}, status=400)
        except (services.AssistantError, BadRequest) as error:
            return JsonResponse({"error": str(error)}, status=400)
        except AssistantUnavailable as error:
            return JsonResponse({"error": str(error), "kind": error.kind}, status=503)

    return wrapper


def _json_body(request: HttpRequest) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError as error:
        raise BadRequest("Request body must be valid JSON.") from error
    if not isinstance(data, dict):
        raise BadRequest("Request body must be a JSON object.")
    return data


def source_json(number: int, source: AIMessageSource) -> dict:
    document = source.document
    return {
        "number": number,  # the [n] used in the answer
        "document_id": document.pk,
        "document_title": document.title,
        "document_type": document.document_type.name,
        "activity_id": document.activity_id,
        "activity_title": document.activity.title,
        "chunk_index": source.chunk_index,
        "download_url": reverse("documents:download", args=[document.pk]),
    }


def message_json(message: AIChatMessage) -> dict:
    sources = list(message.sources.all())
    return {
        "message_id": message.pk,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
        "answered_from": services.answered_from(message),  # documents | records | both | not_found
        "sources": [source_json(number, source) for number, source in enumerate(sources, start=1)],
    }


def chat_json(chat: AIChat) -> dict:
    return {"chat_id": chat.pk, "title": chat.title, "created_at": chat.created_at, "updated_at": chat.updated_at}


SOURCES = Prefetch(
    "sources",
    queryset=AIMessageSource.objects.select_related("document__document_type", "document__activity").order_by("message_source_id"),
)


@staff_login_required
@require_http_methods(["GET", "POST"])
def chats_api(request: HttpRequest) -> JsonResponse:
    """GET: my chats, newest first. POST: a new empty chat (its title comes from the first question)."""
    if request.method == "POST":
        return JsonResponse({"chat": chat_json(services.create_chat(request.user))}, status=201)
    return JsonResponse({"chats": [chat_json(chat) for chat in services.my_chats(request.user)[:100]]})


@staff_login_required
@require_GET
def chat_detail_api(request: HttpRequest, chat_id: int) -> JsonResponse:
    chat = services.get_chat(request.user, chat_id)
    messages = chat.messages.order_by("created_at", "message_id").prefetch_related(SOURCES)
    return JsonResponse({"chat": chat_json(chat), "messages": [message_json(m) for m in messages]})


@staff_login_required
@require_POST
@api_errors
def ask_api(request: HttpRequest, chat_id: int) -> JsonResponse:
    """POST {"question": "...", "activity_id": optional} → the assistant's answer with its sources."""
    chat = services.get_chat(request.user, chat_id)
    data = _json_body(request)
    activity_id = data.get("activity_id")
    if activity_id not in (None, "") and not str(activity_id).isdigit():
        raise BadRequest("activity_id must be a number.")
    answer = services.ask(request.user, chat, str(data.get("question", "")), int(activity_id) if activity_id else None)
    message = AIChatMessage.objects.prefetch_related(SOURCES).get(pk=answer.message.pk)
    chat.refresh_from_db()
    return JsonResponse({"chat": chat_json(chat), "message": message_json(message)}, status=201)
