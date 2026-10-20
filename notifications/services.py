"""Business logic for in-app notifications (reminders to members and startups).

Views stay thin and call the functions in this module. A member only ever sees and changes
their own notifications: every query filters by `member`.
"""

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from accounts.models import TBIMember
from accounts.permissions import require_staff_member
from activities.services import PageResult, _paginate

from .models import Notification

DEFAULT_PAGE_SIZE = 20


def my_notifications(member: TBIMember) -> QuerySet:
    require_staff_member(member)
    return Notification.objects.filter(member=member).select_related("activity", "document_type")


def list_notifications(member: TBIMember, page: int | str | None = 1, unread_only: bool = False,
                       per_page: int = DEFAULT_PAGE_SIZE) -> PageResult:
    """The member's notifications, newest first."""
    queryset = my_notifications(member)
    if unread_only:
        queryset = queryset.filter(is_read=False)
    return _paginate(queryset.order_by("-created_at", "-notification_id"), page, per_page)


def unread_count(member: TBIMember) -> int:
    return my_notifications(member).filter(is_read=False).count()


def mark_read(member: TBIMember, notification_id: int) -> Notification:
    """Mark one of the member's own notifications as read. Someone else's gives 404."""
    notification = get_object_or_404(my_notifications(member), pk=notification_id)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    return notification


def mark_all_read(member: TBIMember) -> int:
    """Mark all of the member's notifications as read. Returns how many changed."""
    return my_notifications(member).filter(is_read=False).update(is_read=True)
