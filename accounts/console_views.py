"""The Admin Console pages (phase F6, /accounts/console/...): Members, Roles, Certificate design and
System status. Every view needs can_manage_users (permission_required); the services check it again.

Thin views: members through accounts.services (guardrails: never yourself, at least one active Admin, never
deleted), templates and signatories through certificates.services (image checks, files never change, never
deleted: there is no delete URL), checks through accounts.system_status. Master data and the flags
can_create_activity / can_manage_users stay in the Django admin (ASSUMPTIONS 224); the Roles tab switches only
can_approve_documents on or off (ASSUMPTIONS 344) and links to the Django admin for the rest.
"""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from activities.models import ActivityDeletion
from activities.services import _paginate
from certificates import services as certificate_services
from certificates.models import CertificateTemplate
from config.mail import email_sent_message
from config.parsing import parse_bool
from documents.pages import safe_next
from registrations.public_pages import flat_errors

from . import console_pages as pages
from . import services, system_status
from .models import Position, Role, TBIMember
from .permissions import Permission, is_htmx, permission_required

manage_users = permission_required(Permission.MANAGE_USERS)


def _page(request: HttpRequest, template: str, tab: str, context: dict, status: int = 200) -> HttpResponse:
    context = {
        "page_title": "Admin Console",
        "page_subtitle": "Members, roles, the certificate design and the system's health",
        "tabs": pages.tabs(tab),
        **context,
    }
    return render(request, template, context, status=status)


# --- Members ----------------------------------------------------------------------------------------


def _members_context(request: HttpRequest, invite: dict | None = None, invite_errors: dict | None = None) -> dict:
    filters = pages.parse_member_filters(request.GET)
    result = services.list_members(filters.for_service(), page=filters.page)
    pages.attach_position_choices(result.items)
    return {
        "filters": filters,
        "result": result,
        "members": result.items,
        "roles": pages.roles(),
        "list_url": reverse("accounts:console"),
        "invite": invite or {},
        "invite_errors": invite_errors or {},
    }


@manage_users
@require_GET
def members(request: HttpRequest) -> HttpResponse:
    """GET q, role, is_active=true|false, page. An HTMX request (a filter or a page) gets only the results."""
    context = _members_context(request)
    if is_htmx(request):
        return render(request, "console/_members.html", context)
    return _page(request, "console/members.html", "accounts:console", context)


@manage_users
@require_POST
def invite(request: HttpRequest) -> HttpResponse:
    """Create a member and email the invitation (services.create_member). Refused: the page again, dialog open."""
    values = {key: request.POST.get(key, "").strip() for key in ("name", "email", "role")}
    role = pages.role_from(values["role"])
    errors: dict[str, str] = {}
    if role is None:
        errors["role"] = "Choose a role."
    else:
        try:
            member = services.create_member(request.user, name=values["name"], email=values["email"], role=role)
        except services.MemberManagementError as error:
            message = str(error)
            errors["name" if "name" in message and message != services.INVITATION_NOT_SENT else "email"] = message
        else:
            messages.success(request, email_sent_message(
                f"The invitation with a temporary password was emailed to {member.email}.", before=f"{member.name} was added. "
            ))
            return redirect("accounts:console")
    return _page(request, "console/members.html", "accounts:console", _members_context(request, values, errors), status=400)


def _member(member_id: int) -> TBIMember:
    return get_object_or_404(TBIMember.objects.select_related("role"), pk=member_id)


def _back(request: HttpRequest) -> HttpResponse:
    return redirect(safe_next(request, reverse("accounts:console")))


@manage_users
@require_POST
def change_role(request: HttpRequest, member_id: int) -> HttpResponse:
    member = _member(member_id)
    role = pages.role_from(request.POST.get("role"))
    if role is None:
        messages.error(request, "Choose a role.")
        return _back(request)
    try:
        services.change_member_role(request.user, member, role)
    except services.MemberManagementError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{member.name} is now {role.role_name}.")
    return _back(request)


@manage_users
@require_POST
def change_position(request: HttpRequest, member_id: int) -> HttpResponse:
    """POST position=<id> or empty (no position): services.set_member_position (ASSUMPTIONS 429)."""
    member = _member(member_id)
    raw = request.POST.get("position", "").strip()
    if raw and not raw.isdigit():
        messages.error(request, services.POSITION_NOT_OFFERED)
        return _back(request)
    try:
        services.set_member_position(request.user, member, int(raw) if raw else None)
    except services.MemberManagementError as error:
        messages.error(request, str(error))
    else:
        title = member.position.name if member.position else "no position"
        messages.success(request, f"{member.name}'s position is now {title}.")
    return _back(request)


@manage_users
@require_POST
def deactivate(request: HttpRequest, member_id: int) -> HttpResponse:
    member = _member(member_id)
    try:
        services.deactivate_member(request.user, member)
    except services.MemberManagementError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{member.name} was deactivated and can no longer sign in.")
    return _back(request)


@manage_users
@require_POST
def reactivate(request: HttpRequest, member_id: int) -> HttpResponse:
    member = _member(member_id)
    services.reactivate_member(request.user, member)
    messages.success(request, f"{member.name} was reactivated and can sign in again.")
    return _back(request)


@manage_users
@require_POST
def reset_password(request: HttpRequest, member_id: int) -> HttpResponse:
    member = _member(member_id)
    try:
        services.reset_member_password(request.user, member)
    except services.MemberManagementError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, email_sent_message(f"A new temporary password was emailed to {member.email}."))
    return _back(request)


# --- Roles ---------------------------------------------------------------------------------------------


@manage_users
@require_GET
def roles(request: HttpRequest) -> HttpResponse:
    context = {
        "roles": Role.objects.annotate(member_count=Count("members", filter=Q(members__is_active=True))).order_by("role_name"),
        "admin_url": reverse("admin:accounts_role_changelist"),
        "no_creator": not services.active_activity_creators().exists(),
        "no_deleter": not services.active_activity_deleters().exists(),
        "no_approver": not services.active_document_approvers().exists(),
        "no_certificate_approver": not services.active_certificate_approvers().exists(),
        "no_signatory_manager": not services.active_signatory_managers().exists(),
    }
    return _page(request, "console/roles.html", "accounts:console_roles", context)


def _switch_role_flag(request: HttpRequest, role_id: int, switch, turned_on: str, turned_off: str) -> HttpResponse:
    """POST enabled=true|false through one accounts.services switch (audit log there)."""
    role = get_object_or_404(Role, pk=role_id)
    back = redirect("accounts:console_roles")
    try:
        enabled = parse_bool(request.POST.get("enabled"), "enabled")
        if enabled is None:
            raise ValidationError({"enabled": ["enabled must be true or false."]})
        switch(request.user, role, enabled)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return back
    except services.MemberManagementError as error:
        messages.error(request, str(error))
        return back
    messages.success(request, f"{role.role_name} {turned_on if enabled else turned_off}.")
    return back


@manage_users
@require_POST
def role_activity_creation(request: HttpRequest, role_id: int) -> HttpResponse:
    """POST enabled=true|false: turn can_create_activity on or off for one role (ASSUMPTIONS 388)."""
    return _switch_role_flag(request, role_id, services.set_role_activity_creation,
                             "can now create and edit activities", "can no longer create or edit activities")


@manage_users
@require_POST
def role_activity_deletion(request: HttpRequest, role_id: int) -> HttpResponse:
    """POST enabled=true|false: turn can_delete_activities on or off for one role (ASSUMPTIONS 389)."""
    return _switch_role_flag(request, role_id, services.set_role_activity_deletion,
                             "can now delete activities", "can no longer delete activities")


@manage_users
@require_POST
def role_document_approval(request: HttpRequest, role_id: int) -> HttpResponse:
    """POST enabled=true|false: turn can_approve_documents on or off for one role (ASSUMPTIONS 344)."""
    return _switch_role_flag(request, role_id, services.set_role_document_approval,
                             "can now approve and return documents", "can no longer approve or return documents")


@manage_users
@require_POST
def role_certificate_approval(request: HttpRequest, role_id: int) -> HttpResponse:
    """POST enabled=true|false: turn can_approve_certificates on or off for one role (ASSUMPTIONS 357)."""
    return _switch_role_flag(request, role_id, services.set_role_certificate_approval,
                             "can now approve certificates", "can no longer approve certificates")


@manage_users
@require_POST
def role_signatory_management(request: HttpRequest, role_id: int) -> HttpResponse:
    """POST enabled=true|false: turn can_manage_signatories on or off for one role (ASSUMPTIONS 418)."""
    return _switch_role_flag(request, role_id, services.set_role_signatory_management,
                             "can now manage certificate signatories", "can no longer manage certificate signatories")


# --- Positions (job titles, ASSUMPTIONS 390) ---------------------------------------------------------------


DELETED_PAGE_SIZE = 25


@manage_users
@require_GET
def positions(request: HttpRequest) -> HttpResponse:
    rows = Position.objects.annotate(member_count=Count("members", filter=Q(members__is_active=True))).order_by(
        "-is_active", "name")
    return _page(request, "console/positions.html", "accounts:console_positions",
                 {"positions": rows, "add": {}, "add_errors": {}})


def _position_post(request: HttpRequest, action) -> HttpResponse:
    try:
        message = action()
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, message)
    return redirect("accounts:console_positions")


@manage_users
@require_POST
def position_add(request: HttpRequest) -> HttpResponse:
    """POST name: add a job title."""
    def add() -> str:
        position = services.add_position(request.user, request.POST.get("name", ""))
        return f"“{position.name}” was added."
    return _position_post(request, add)


@manage_users
@require_POST
def position_rename(request: HttpRequest, position_id: int) -> HttpResponse:
    """POST name: rename a job title (every member who has it shows the new name)."""
    position = get_object_or_404(Position, pk=position_id)

    def rename() -> str:
        old = position.name
        services.rename_position(request.user, position, request.POST.get("name", ""))
        return f"“{old}” is now “{position.name}”."
    return _position_post(request, rename)


@manage_users
@require_POST
def position_active(request: HttpRequest, position_id: int) -> HttpResponse:
    """POST active=true|false: deactivate (no longer offered; members keep it) or reactivate. Never deleted."""
    position = get_object_or_404(Position, pk=position_id)

    def switch() -> str:
        active = parse_bool(request.POST.get("active"), "active")
        if active is None:
            raise ValidationError("active must be true or false.")
        services.set_position_active(request.user, position, active)
        return (f"“{position.name}” is offered again." if active
                else f"“{position.name}” is no longer offered. Members who have it keep it.")
    return _position_post(request, switch)


# --- Deleted activities (read only, ASSUMPTIONS 389) ---------------------------------------------------------


@manage_users
@require_GET
def deleted_activities(request: HttpRequest) -> HttpResponse:
    """The ACTIVITY_DELETION records, newest first, 25 a page. Read only: nothing here changes or removes a record."""
    result = _paginate(ActivityDeletion.objects.select_related("deleted_by"), request.GET.get("page", 1), DELETED_PAGE_SIZE)
    return _page(request, "console/deleted_activities.html", "accounts:console_deleted_activities",
                 {"result": result, "deletions": [(record, pages.deletion_counts(record)) for record in result.items]})


# --- Certificate design ------------------------------------------------------------------------------


def _design_page(request: HttpRequest, *, open_form: str = "", values: dict | None = None, errors: dict | None = None,
                 status: int = 200) -> HttpResponse:
    """open_form: "template" (the refused form: open again, its values and errors under its fields)."""
    errors = dict(errors or {})
    if "__all__" in errors:  # a message about no field in particular: shown with the file
        errors["file"] = " ".join(filter(None, [errors.pop("__all__"), errors.get("file", "")]))
    width, height = certificate_services.MIN_TEMPLATE_SIZE
    context = {
        **pages.certificate_design(request.user),
        f"{open_form}_values": values or {},
        f"{open_form}_errors": errors,
        "max_mb": settings.CERTIFICATE_IMAGE_MAX_MB,
        "template_help": f"PNG or JPG, A4 landscape, at least {width} × {height} pixels.",
    }
    return _page(request, "console/certificates.html", "accounts:console_certificates", context, status=status)


@manage_users
@require_GET
def certificate_design(request: HttpRequest) -> HttpResponse:
    return _design_page(request)


@manage_users
@require_POST
def upload_template(request: HttpRequest) -> HttpResponse:
    """A new design (certificates.services.upload_template: the image checks; a new row, files never change)."""
    values = {"name": request.POST.get("name", ""), "activate": request.POST.get("activate") == "true"}
    try:
        template = certificate_services.upload_template(request.user, values["name"], request.FILES.get("file"), values["activate"])
    except ValidationError as error:
        return _design_page(request, open_form="template", values=values, errors=flat_errors(error.message_dict), status=400)
    messages.success(request, f"The design “{template.name}” was uploaded{' and is now the active one' if template.is_active else ''}.")
    return redirect("accounts:console_certificates")


@manage_users
@require_POST
def template_action(request: HttpRequest, template_id: int, action: str) -> HttpResponse:
    """activate (the only active one) or deactivate. There is no delete: designs are kept for the certificates made with them."""
    template = get_object_or_404(CertificateTemplate, pk=template_id)
    if action == "activate":
        certificate_services.activate_template(request.user, template)
        messages.success(request, f"“{template.name}” is now the active design.")
    elif action == "deactivate":
        certificate_services.deactivate_template(request.user, template)
        messages.warning(request, f"“{template.name}” was deactivated. Certificates cannot be generated until a design is active.")
    else:
        raise Http404("Unknown action.")
    return redirect("accounts:console_certificates")


# --- System status -----------------------------------------------------------------------------------


@manage_users
@require_GET
def system(request: HttpRequest) -> HttpResponse:
    context = {"items": system_status.system_status(), "checked_at": system_status.checked_at()}
    return _page(request, "console/system.html", "accounts:console_system", context)


@manage_users
@require_GET
def system_pdf(request: HttpRequest) -> HttpResponse:
    """The PDF check alone (loaded by HTMX when the page opens, so the page itself appears at once)."""
    return render(request, "console/_status_item.html", {"item": system_status.pdf_status()})
