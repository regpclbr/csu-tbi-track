"""Display helpers for the bell's dropdown (phase F4) and the list page (F5): where a notification leads. Display only."""

from urllib.parse import urlencode

from django.urls import reverse

from .models import Notification


def target_url(notification: Notification) -> str:
    """The page a notification is about: a document reminder or a returned document opens the activity's Documents tab
    with that type preselected; prepared certificates open the Certificates tab (ASSUMPTIONS 420); another notification
    about an activity opens the activity; anything else the dashboard (startup notifications without an activity,
    item 223)."""
    if notification.activity_id is None:
        return reverse("home")
    url = reverse("activities:detail", args=[notification.activity_id])
    if notification.document_type_id:
        return f"{url}?{urlencode({'tab': 'documents', 'type': notification.document_type_id})}"
    if notification.type in Notification.CERTIFICATE_TYPES:
        return f"{url}?tab=certificates"
    return url


def target_label(notification: Notification) -> str:
    """The words of the link to target_url(): "Open documents", "Open certificates", "Open activity", or "" when it
    leads nowhere special."""
    if notification.activity_id is None:
        return ""
    if notification.type in Notification.CERTIFICATE_TYPES:
        return "Open certificates"
    return "Open documents" if notification.document_type_id else "Open activity"
