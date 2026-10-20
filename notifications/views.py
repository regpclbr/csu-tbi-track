"""JSON API for "my notifications". A member only sees and changes their own."""

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_POST

from config.json import JsonResponse  # date-times in Manila time

from accounts.permissions import staff_login_required

from . import services
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
    """GET ?page=&unread=1 — newest first."""
    unread_only = request.GET.get("unread") == "1"
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
