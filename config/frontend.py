"""What every staff page's layout needs: the sidebar items, the member's badge counts and the date.
The startup portal's layout (phase F6) is `portal` (portal_layout), with its own list of items.

Used as a context processor (settings.TEMPLATES), so every template can use `layout`. The counts are
computed only when a template reads them (the Django admin, emails and PDFs never do).
"""

from dataclasses import dataclass
from datetime import date
from functools import cached_property

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse

from accounts.permissions import Permission, has_permission, is_staff_member
from documents.services import review_queue, today_in_manila
from notifications.services import unread_count


def initials(name: str) -> str:
    """Avatar letters: first and last word ("Juan Dela Cruz" -> "JC"), one word -> its first two letters."""
    words = [word for word in name.split() if word[:1].isalpha()]
    letters = (words[0][0] + words[-1][0]) if len(words) > 1 else (words[0][:2] if words else "?")
    return letters.upper()


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str  # where the link goes
    icon: str  # a name known to components/icon.html
    section: str  # the URL namespace (or view name) that marks the item as active
    permission: Permission | None = None  # hidden without it (the view checks it again)
    badge: str = ""  # name of a StaffLayout count shown next to the label


WORKSPACE = [
    NavItem("Dashboard", "home", "layout-grid", "home"),
    NavItem("Activities", "activities:list", "calendar", "activities"),
    NavItem("Documents", "documents:list", "folder", "documents", badge="review_count"),
    NavItem("Certificates", "certificates:list", "award", "certificates"),
    NavItem("Startups", "startups:list", "store", "startups"),
    NavItem("Reports", "reports:list", "chart-column", "reports"),
    NavItem("AI Assistant", "assistant:chat", "sparkles", "assistant"),
]
ADMIN = [
    NavItem("Admin Console", "accounts:console", "shield", "console", permission=Permission.MANAGE_USERS),
]


class StaffLayout:
    """The layout data for one request by an active staff member."""

    def __init__(self, request: HttpRequest) -> None:
        self.request = request
        self.member = request.user

    def _section(self) -> str:
        match = self.request.resolver_match
        if match is None:
            return ""
        if match.view_name.startswith("accounts:console"):
            return "console"
        return match.namespace or match.view_name

    def _items(self, items: list[NavItem]) -> list[dict]:
        section = self._section()
        return [
            {
                "label": item.label,
                "url": reverse(item.url_name),
                "icon": item.icon,
                "active": item.section == section,
                "badge": getattr(self, item.badge) if item.badge else 0,
                "badge_label": "documents waiting for your review" if item.badge == "review_count" else "",
            }
            for item in items
            if item.permission is None or has_permission(self.member, item.permission)
        ]

    @cached_property
    def workspace(self) -> list[dict]:
        return self._items(WORKSPACE)

    @cached_property
    def admin(self) -> list[dict]:
        return self._items(ADMIN)

    @cached_property
    def review_count(self) -> int:
        """Documents waiting for MY review (documents.services.review_queue; 0, so no badge, without
        can_approve_documents)."""
        return review_queue(self.member).count()

    @cached_property
    def unread_count(self) -> int:
        return unread_count(self.member)

    @cached_property
    def today(self) -> date:
        return today_in_manila()

    @cached_property
    def initials(self) -> str:
        return initials(self.member.name)

    @cached_property
    def can_create_activity(self) -> bool:
        return has_permission(self.member, Permission.CREATE_ACTIVITY)


def staff_layout(request: HttpRequest) -> dict:
    """Context processor: `layout` for an active staff member who may use the app (not during the
    first-sign-in password change); nothing otherwise."""
    user = getattr(request, "user", None)
    if not is_staff_member(user) or user.must_change_password:
        return {}
    return {"layout": StaffLayout(request)}


PORTAL = [
    NavItem("Overview", "startups:portal_home", "layout-grid", "startups:portal_home"),
    NavItem("Open activities", "startups:portal_activities", "calendar", "activities"),
    NavItem("My registrations", "startups:portal_registrations_page", "clipboard-list", "startups:portal_registrations_page"),
    NavItem("My certificates", "startups:portal_certificates_page", "award", "startups:portal_certificates_page"),
    NavItem("Notifications", "startups:portal_notifications_page", "bell", "startups:portal_notifications_page", badge="unread_count"),
    NavItem("Profile", "startups:portal_profile", "user-round", "startups:portal_profile"),
]
# Pages that mark "Open activities" as active (the list and registering).
PORTAL_ACTIVITY_VIEWS = {"startups:portal_activities", "startups:portal_register_page", "startups:portal_registered"}


class PortalLayout:
    """The layout data for one portal request by a logged-in startup (request.startup, set by portal_page_required)."""

    def __init__(self, request: HttpRequest) -> None:
        self.request = request
        self.startup = request.startup

    def _section(self) -> str:
        match = self.request.resolver_match
        view_name = match.view_name if match else ""
        return "activities" if view_name in PORTAL_ACTIVITY_VIEWS else view_name

    @cached_property
    def items(self) -> list[dict]:
        section = self._section()
        return [
            {
                "label": item.label,
                "url": reverse(item.url_name),
                "icon": item.icon,
                "active": item.section == section,
                "badge": getattr(self, item.badge) if item.badge else 0,
                "badge_label": "unread" if item.badge else "",
            }
            for item in PORTAL
        ]

    @cached_property
    def unread_count(self) -> int:
        return unread_count(self.startup)

    @cached_property
    def today(self) -> date:
        return today_in_manila()

    @cached_property
    def initials(self) -> str:
        return initials(self.startup.name)


def portal_layout(request: HttpRequest) -> dict:
    """Context processor: `portal` on the startup portal's pages (a logged-in startup); nothing otherwise."""
    if getattr(request, "startup", None) is None:
        return {}
    return {"portal": PortalLayout(request)}


def public_footer(request: HttpRequest) -> dict:
    """Context processor: the TBI's contact address for the footer of the public pages (phase F3)."""
    return {"tbi_contact_email": settings.TBI_CONTACT_EMAIL}
