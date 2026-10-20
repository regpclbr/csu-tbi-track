"""Business logic for in-app notifications (to TBI members and to startups).

Views stay thin and call the functions in this module. A NOTIFICATION has exactly one
recipient (a member or a startup), and a recipient only ever sees and changes their own:
every query filters by that recipient.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from accounts.models import TBIMember
from accounts.permissions import require_staff_member
from activities.services import PageResult, _paginate
from startups.models import Startup

from .models import Notification

DEFAULT_PAGE_SIZE = 20

Recipient = TBIMember | Startup


def my_notifications(recipient: Recipient) -> QuerySet:
    """The recipient's own notifications: an active staff member's, or an active startup's."""
    if isinstance(recipient, Startup):
        if not recipient.is_active:
            raise PermissionDenied("This startup account is not active.")
        queryset = Notification.objects.filter(startup=recipient)
    else:
        require_staff_member(recipient)
        queryset = Notification.objects.filter(member=recipient)
    return queryset.select_related("activity", "document_type")


def list_notifications(recipient: Recipient, page: int | str | None = 1, unread_only: bool = False,
                       per_page: int = DEFAULT_PAGE_SIZE) -> PageResult:
    """The recipient's notifications, newest first."""
    queryset = my_notifications(recipient)
    if unread_only:
        queryset = queryset.filter(is_read=False)
    return _paginate(queryset.order_by("-created_at", "-notification_id"), page, per_page)


def unread_count(recipient: Recipient) -> int:
    return my_notifications(recipient).filter(is_read=False).count()


def mark_read(recipient: Recipient, notification_id: int) -> Notification:
    """Mark one of the recipient's own notifications as read. Someone else's gives 404."""
    notification = get_object_or_404(my_notifications(recipient), pk=notification_id)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    return notification


def mark_all_read(recipient: Recipient) -> int:
    """Mark all of the recipient's notifications as read. Returns how many changed."""
    return my_notifications(recipient).filter(is_read=False).update(is_read=True)
