"""JSON API for "my notifications", the top bar's bell dropdown (HTML for HTMX) and the full list page (/notifications/).
A member only sees and changes their own."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from config.json import JsonResponse  # date-times in Manila time

from accounts.permissions import staff_login_required
from config.parsing import parse_bool

from . import pages, services
from .models import Notification


def notification_json(notification: Notification) -> dict:
    return {
        "notification_id": notification.pk,
        "type": notification.type,
        "message": notification.message,
        "activity_id": notification.activity_id,
        "activity_title": notification.activity.title if notification.activity else None,
        "document_type_id": notification.document_type_id,
        "document_type": notification.document_type.name if notification.document_type else None,
        "is_read": notification.is_read,
        "created_at": notification.created_at,
    }


@staff_login_required
@require_GET
def list_api(request: HttpRequest) -> JsonResponse:
    """GET ?page=&unread=true|false — newest first."""
    try:
        unread_only = parse_bool(request.GET.get("unread"), "unread", default=False)
    except ValidationError as error:
        return JsonResponse({"errors": error.message_dict}, status=400)
    result = services.list_notifications(request.user, request.GET.get("page", 1), unread_only=unread_only)
    return JsonResponse(
        {
            "notifications": [notification_json(n) for n in result.items],
            "unread_count": services.unread_count(request.user),
            "total": result.total,
            "page": result.page,
            "num_pages": result.num_pages,
        }
    )


@staff_login_required
@require_GET
def unread_count_api(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"unread_count": services.unread_count(request.user)})


@staff_login_required
@require_POST
def mark_read_api(request: HttpRequest, notification_id: int) -> JsonResponse:
    notification = services.mark_read(request.user, notification_id)
    return JsonResponse({"notification": notification_json(notification)})


@staff_login_required
@require_POST
def mark_all_read_api(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"marked_read": services.mark_all_read(request.user)})


# --- The bell dropdown in the top bar (HTML partials for HTMX) -------------------------------------

DROPDOWN_SIZE = 8


def _dropdown(request: HttpRequest) -> HttpResponse:
    latest = services.list_notifications(request.user, 1, per_page=DROPDOWN_SIZE)
    return render(request, "partials/notifications_dropdown.html", {
        "notifications": latest.items,
        "total": latest.total,
        "unread": services.unread_count(request.user),
    })


@staff_login_required
@require_GET
def dropdown(request: HttpRequest) -> HttpResponse:
    """GET: the latest notifications, for the bell's dropdown."""
    return _dropdown(request)


@staff_login_required
@require_POST
def dropdown_read_all(request: HttpRequest) -> HttpResponse:
    """POST: mark all my notifications as read, then show the dropdown again."""
    services.mark_all_read(request.user)
    return _dropdown(request)


@staff_login_required
@require_POST
def open_notification(request: HttpRequest, notification_id: int) -> HttpResponse:
    """POST from the dropdown: mark one of my notifications as read and go to the page it is about
    (notifications.pages.target_url). Someone else's notification gives 404."""
    notification = services.mark_read(request.user, notification_id)
    return redirect(pages.target_url(notification))


# --- The full list (phase F5) ----------------------------------------------------------------------


def _list_url(request: HttpRequest) -> str:
    """Back to the list as it was (All / Unread and the page), only within this site."""
    from documents.pages import safe_next

    return safe_next(request, reverse("notifications:list"))


@staff_login_required
@require_GET
def notification_list(request: HttpRequest) -> HttpResponse:
    """GET show=all|unread (anything else: all), page. My notifications, newest first (services.list_notifications)."""
    show = "unread" if request.GET.get("show") == "unread" else "all"
    result = services.list_notifications(request.user, request.GET.get("page", "1"), unread_only=show == "unread")
    context = {
        "page_title": "Notifications",
        "page_subtitle": "Reminders about required documents and other updates for you",
        "show": show,
        "result": result,
        "notifications": [{"notification": n, "target": pages.target_label(n)} for n in result.items],
        "unread": services.unread_count(request.user),
        "query": "show=unread" if show == "unread" else "",
    }
    return render(request, "notifications/list.html", context)


@staff_login_required
@require_POST
def mark_read(request: HttpRequest, notification_id: int) -> HttpResponse:
    """POST: mark one of my notifications as read (someone else's: 404), then back to the list."""
    services.mark_read(request.user, notification_id)
    return redirect(_list_url(request))


@staff_login_required
@require_POST
def mark_all_read(request: HttpRequest) -> HttpResponse:
    changed = services.mark_all_read(request.user)
    messages.success(request, f"{changed} notification{'s' if changed != 1 else ''} marked as read." if changed else "Nothing was unread.")
    return redirect(_list_url(request))
