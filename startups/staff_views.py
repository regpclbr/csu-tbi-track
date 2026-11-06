"""The staff Startups pages (phase F5): /startups/ (list with search and filters) and /startups/<id>/ (profile and
registration history), plus "Sync now" and "Send set-password link". Thin views: startups.services does the work;
startups/pages.py reads the filters and words the results. (The startup's own set-password page is in page_views.py.)

Viewing: any active staff member. Sync and set-password links: can_manage_users (permission_required; the buttons
are hidden without it). Navigatu not available: the provider's message as an error toast.
"""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import Permission, has_permission, is_htmx, permission_required, staff_login_required
from config.mail import email_sent_message
from documents.pages import safe_next

from . import pages, services
from .models import Startup
from .providers import ProviderNotAvailable


@staff_login_required
@require_GET
def startup_list(request: HttpRequest) -> HttpResponse:
    """GET q, maturity_stage, is_active=true|false, page. An HTMX request (a filter or a page) gets only the results."""
    filters = pages.parse_filters(request.GET)
    context = {
        "page_title": "Startups",
        "page_subtitle": "Incubatees from Navigatu (read-only here) and their activity history",
        "filters": filters,
        "can_manage": has_permission(request.user, Permission.MANAGE_USERS),
        "list_url": reverse("startups:list"),
        **pages.startup_list(filters),
    }
    if is_htmx(request):
        return render(request, "startups/_results.html", context)
    context["stages"] = services.maturity_stages()
    return render(request, "startups/list.html", context)


@staff_login_required
@require_GET
def startup_detail(request: HttpRequest, startup_id: int) -> HttpResponse:
    startup = get_object_or_404(Startup, pk=startup_id)
    context = {
        "page_title": startup.name,
        "page_subtitle": "Startup profile from Navigatu and its registrations",
        "startup": startup,
        "can_manage": has_permission(request.user, Permission.MANAGE_USERS),
        **pages.history(startup),
    }
    return render(request, "startups/detail.html", context)


@permission_required(Permission.MANAGE_USERS)
@require_POST
def sync_now(request: HttpRequest) -> HttpResponse:
    """Run the Navigatu sync now (never forced: the deactivation guard always applies here)."""
    try:
        result = services.sync_startups()
    except ProviderNotAvailable as error:
        messages.error(request, str(error))
    else:
        text, level = pages.sync_message(result)
        (messages.warning if level == "warning" else messages.success)(request, text)
    return redirect(safe_next(request, reverse("startups:list")))


@permission_required(Permission.MANAGE_USERS)
@require_POST
def send_password_link(request: HttpRequest, startup_id: int) -> HttpResponse:
    """Email the startup its set-password link (services.send_password_link: active startups only). The link itself
    is never shown here."""
    startup = get_object_or_404(Startup, pk=startup_id)
    try:
        services.send_password_link(request.user, startup)
    except services.StartupError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, email_sent_message(f"A set-password link was emailed to {startup.email}."))
    return redirect("startups:detail", startup_id=startup.pk)
