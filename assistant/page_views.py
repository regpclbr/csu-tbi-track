"""The AI Assistant page (phase F5): /assistant/ (a new, empty chat) and /assistant/<id>/ (one of my chats), and the
form that asks a question. Thin views: assistant.services answers (ask), keeps chats private (get_chat: someone
else's chat is 404) and saves a question only together with its answer; assistant/pages.py prepares the display
(the answer is escaped there: model text is never rendered as HTML).

A new chat is created by its first question; if that question fails, the empty chat is removed again
(services.discard_if_empty), so no empty "New chat" rows pile up (owner decision F5-1).

My chats (ASSUMPTIONS 377-378): each row's menu renames (services.rename_chat) or deletes (services.delete_chat) one
of my chats; both are POST forms that end on the chat that was open ("current"), or on a new chat when the open one
was deleted. chat_list is the partial the page fetches once a few seconds after a new chat's first answer, so the
title generated in the background appears by itself.
"""

import json

from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import is_htmx, staff_login_required

from . import pages, services
from .gemini import AssistantUnavailable
from .models import AIChat

PAGE_TITLE = "AI Assistant"
PAGE_SUBTITLE = "Answers from approved documents and the system's records, always with their sources"


def _context(request: HttpRequest, chat: AIChat | None, **extra) -> dict:
    conversation = pages.conversation(chat) if chat else []
    return {
        "page_title": PAGE_TITLE,
        "page_subtitle": PAGE_SUBTITLE,
        "chats": services.my_chats(request.user)[:100],
        "chat": chat,
        "conversation": conversation,  # never "messages": that name belongs to Django's messages (the toasts)
        "suggestions": [] if conversation else pages.suggestions(),
        "activities": pages.activity_choices(),
        "question": "",
        "activity_id": "",
        "error": None,
        "errors": {},
        "question_max": services.QUESTION_MAX_LENGTH,
        "title_max": services.TITLE_LENGTH,
        "timeout_seconds": _timeout_seconds(),
        **extra,
    }


def _timeout_seconds() -> int:
    from django.conf import settings

    return settings.ASSISTANT_QUESTION_TIMEOUT_SECONDS


@staff_login_required
@require_GET
def chat_page(request: HttpRequest, chat_id: int | None = None) -> HttpResponse:
    """My chats on the left, this chat (or a new, empty one) on the right. Someone else's chat: 404."""
    chat = services.get_chat(request.user, chat_id) if chat_id else None
    return render(request, "assistant/chat.html", _context(request, chat))


def _chat_from(request: HttpRequest) -> tuple[AIChat, bool]:
    """The posted chat (one of mine, else 404), or a new chat. Returns (chat, is_new)."""
    raw = request.POST.get("chat", "").strip()
    if not raw:
        return services.create_chat(request.user), True
    if not raw.isdigit():
        raise Http404("Unknown chat.")
    return services.get_chat(request.user, int(raw)), False


@staff_login_required
@require_POST
def ask(request: HttpRequest) -> HttpResponse:
    """POST question, chat (optional: empty starts a new chat), activity (optional: search only its documents).

    HTMX: the saved question and answer are appended to the conversation; a refusal goes to the error card above the
    message box (200, HX-Retarget). Without JavaScript: a redirect to the chat, or the page again with the error
    (400, or 503 when the AI service is not available)."""
    chat, is_new = _chat_from(request)
    question = request.POST.get("question", "")
    raw_activity = request.POST.get("activity", "").strip()
    error, errors, status = None, {}, 200
    if raw_activity and not raw_activity.isdigit():
        errors, status = {"activity_id": "Choose an activity from the list."}, 400
    else:
        try:
            answer = services.ask(request.user, chat, question, int(raw_activity) if raw_activity else None)
        except ValidationError as exception:
            errors, status = {key: " ".join(value) for key, value in exception.message_dict.items()}, 400
        except services.AssistantError as exception:
            error, status = {"message": str(exception), "hint": "", "kind": "refused"}, 400
        except AssistantUnavailable as exception:
            error, status = {"message": str(exception), "hint": pages.ERROR_HINTS.get(exception.kind, ""), "kind": exception.kind}, 503

    if status != 200:  # nothing was saved (services.ask saves only answered questions)
        if is_new and services.discard_if_empty(request.user, chat):
            chat = None
        if is_htmx(request):
            response = render(request, "assistant/_ask_error.html", {"error": error, "errors": errors})
            response["HX-Retarget"] = "#ask-error"
            response["HX-Reswap"] = "innerHTML"
            return response
        context = _context(request, chat, question=question, activity_id=raw_activity, error=error, errors=errors)
        return render(request, "assistant/chat.html", context, status=status)

    chat.refresh_from_db()
    if not is_htmx(request):
        return redirect("assistant:conversation", chat_id=chat.pk)
    context = {
        "chat": chat,
        "is_new": is_new,
        "conversation": pages.exchange(answer),
        "chats": services.my_chats(request.user)[:100],
        "title_queued": answer.title_queued,
    }
    response = render(request, "assistant/_exchange.html", context)
    response["HX-Trigger"] = json.dumps({"assistant-answered": chat.pk})  # clears the message box
    if is_new:
        response["HX-Push-Url"] = reverse("assistant:conversation", args=[chat.pk])
    return response


def _back_to_current(request: HttpRequest, removed: int | None = None) -> HttpResponse:
    """Back to the chat that was open (POST "current", only one of mine), else to a new chat."""
    raw = request.POST.get("current", "").strip()
    if raw.isdigit() and int(raw) != removed and services.my_chats(request.user).filter(pk=int(raw)).exists():
        return redirect("assistant:conversation", chat_id=int(raw))
    return redirect("assistant:chat")


@staff_login_required
@require_POST
def delete_chat(request: HttpRequest, chat_id: int) -> HttpResponse:
    """Delete one of my chats with its messages (someone else's: 404). The open chat deleted: a new chat."""
    services.delete_chat(request.user, chat_id)
    messages.success(request, "Chat deleted.")
    return _back_to_current(request, removed=chat_id)


@staff_login_required
@require_POST
def rename_chat(request: HttpRequest, chat_id: int) -> HttpResponse:
    """POST title: rename one of my chats (someone else's: 404). A refused title is shown as a toast."""
    try:
        services.rename_chat(request.user, chat_id, request.POST.get("title", ""))
    except ValidationError as exception:
        messages.error(request, " ".join(exception.messages))
    else:
        messages.success(request, "Chat renamed.")
    return _back_to_current(request)


@staff_login_required
@require_GET
def chat_list(request: HttpRequest) -> HttpResponse:
    """My chats as a partial (?chat=<open chat>), plus the open chat's title out of band. Fetched once after a new
    chat's first answer so the generated title appears without reloading."""
    raw = request.GET.get("chat", "").strip()
    chat = services.my_chats(request.user).filter(pk=int(raw)).first() if raw.isdigit() else None
    context = {"chats": services.my_chats(request.user)[:100], "chat": chat}
    return render(request, "assistant/_chat_list_refresh.html", context)
