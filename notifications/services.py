"""Business logic for in-app notifications to TBI members.

Views stay thin and call the functions in this module. A NOTIFICATION has exactly one recipient (a member; the startup
recipients were removed with the portal, ASSUMPTIONS 458), and a member only ever sees and changes their own: every
query filters by that member.
"""

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from accounts.models import Role, TBIMember
from accounts.permissions import require_staff_member
from activities.services import PageResult, _paginate

from .models import Notification

DEFAULT_PAGE_SIZE = 20

Recipient = TBIMember


def my_notifications(recipient: Recipient) -> QuerySet:
    """The member's own notifications (an active staff member only)."""
    require_staff_member(recipient)
    return Notification.objects.filter(member=recipient).select_related("activity", "document_type")


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


def notify_creator_scheduled(activity, uploader: TBIMember) -> int:
    """In-app "<Activity> is now Scheduled: <Name> uploaded the last of its Activity Design and Request Letter. …" for
    the activity's creator (ASSUMPTIONS 431), linked to the activity. Nobody is told when the creator uploaded it
    themselves (they see the message on the page) or when the creator's account is inactive. Call it inside the
    upload's transaction. No email. Returns how many were created (0 or 1)."""
    creator = activity.created_by
    if creator is None or creator.pk == uploader.pk or not creator.is_active:
        return 0
    message = (f"{activity.title[:200]} is now Scheduled: {uploader.name[:80]} uploaded the last of its Activity Design "
               "and Request Letter. Registration opens once its registration form is marked ready.")[:500]
    Notification.objects.create(member=creator, activity=activity, type=Notification.ACTIVITY_SCHEDULED, message=message)
    return 1


def certificate_reviewers() -> QuerySet:
    """Who is told about certificates to approve (ASSUMPTIONS 420, 421): every active member whose role has
    can_approve_certificates (the Director only by default). Flags only, never a role name. Nobody qualifies = nobody is
    told (the tab and Needs attention still show it; the roles screen warns that nobody can approve certificates)."""
    return TBIMember.objects.filter(is_active=True, role__can_approve_certificates=True).order_by("pk")


CERTIFICATES_PREPARED_MESSAGE = "Certificates of {title} are ready for your review"


def notify_certificate_reviewers(activity, preparer: TBIMember) -> int:
    """In-app "Certificates of <activity> are ready for your review" for every certificate reviewer except the member
    who prepared them, linked to the activity (certificates.services.prepare_activity_certificates calls it once,
    inside its transaction). No email. Returns how many were created."""
    recipients = certificate_reviewers().exclude(pk=preparer.pk)
    message = CERTIFICATES_PREPARED_MESSAGE.format(title=activity.title[:400])[:500]
    created = Notification.objects.bulk_create(
        Notification(member=recipient, activity=activity, type=Notification.CERTIFICATES_PREPARED, message=message)
        for recipient in recipients
    )
    return len(created)


CERTIFICATES_WAITING_MESSAGE = "Certificates of {title} are waiting for your approval"


def notify_certificates_waiting(activity) -> int:
    """In-app "Certificates of <activity> are waiting for your approval" for every certificate approver, ONCE per
    activity (ASSUMPTIONS 421): certificates.services.generate_missing_certificates calls it when the System first
    creates certificates for an activity that is not approved (not after an early approval, item 420). A second call
    for the same activity creates nothing. No email. Returns how many were created."""
    if Notification.objects.filter(activity=activity, type=Notification.CERTIFICATES_WAITING).exists():
        return 0
    message = CERTIFICATES_WAITING_MESSAGE.format(title=activity.title[:400])[:500]
    created = Notification.objects.bulk_create(
        Notification(member=recipient, activity=activity, type=Notification.CERTIFICATES_WAITING, message=message)
        for recipient in certificate_reviewers()
    )
    return len(created)


def mark_all_read(recipient: Recipient) -> int:
    """Mark all of the recipient's notifications as read. Returns how many changed."""
    return my_notifications(recipient).filter(is_read=False).update(is_read=True)
