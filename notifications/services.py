"""Business logic for in-app notifications (to TBI members and to startups).

Views stay thin and call the functions in this module. A NOTIFICATION has exactly one
recipient (a member or a startup), and a recipient only ever sees and changes their own:
every query filters by that recipient.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from accounts.models import Role, TBIMember
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


def active_directors() -> QuerySet:
    """Every active member whose role is the Director (Role.DIRECTOR): who is TOLD, never a permission. Used for the
    new-activity notification (ASSUMPTIONS 361) and the Draft creation-document reminders (ASSUMPTIONS 417)."""
    return TBIMember.objects.filter(is_active=True, role__role_name=Role.DIRECTOR).order_by("pk")


def notify_directors_of_new_activity(activity, creator: TBIMember) -> int:
    """In-app "<Name> created a new activity: <Title> (<Nov 20, 2026>)" for every active member whose role is the
    Director (Role.DIRECTOR: who is told, never a permission; ASSUMPTIONS 361), linked to the activity. Nobody is
    told when a Director creates it. Call it inside the transaction that saves the activity. No email."""
    from activities.formatting import format_date  # activities.services imports this module's caller

    if creator.role.role_name == Role.DIRECTOR:
        return 0
    recipients = active_directors().exclude(pk=creator.pk)
    message = f"{creator.name[:80]} created a new activity: {activity.title[:200]} ({format_date(activity.start_date)})"[:500]
    created = Notification.objects.bulk_create(
        Notification(member=recipient, activity=activity, type=Notification.ACTIVITY_CREATED, message=message)
        for recipient in recipients
    )
    return len(created)


def mark_all_read(recipient: Recipient) -> int:
    """Mark all of the recipient's notifications as read. Returns how many changed."""
    return my_notifications(recipient).filter(is_read=False).update(is_read=True)
