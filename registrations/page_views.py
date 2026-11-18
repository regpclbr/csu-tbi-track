"""Actions of the Forms and Registrations tabs of the activity page, and the printable attendance sheet
(phase F2b). Thin views: every rule is in registrations.services; registrations/pages.py prepares what the
templates show. The tabs themselves are rendered by activities.page_views.activity_detail.

Field actions answer an HTMX request with the updated form card (and a toast through HX-Trigger); without
HTMX they redirect to the Forms tab with a message. The other actions always redirect with a message."""

import json
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import Permission, has_permission, is_htmx, permission_required, staff_login_required
from config.links import post_only_or_tab
from activities.models import Activity
from config.mail import email_sent_message

from . import pages, services
from reports import services as report_services
from reports.page_views import activity_file_response

from .models import EvaluationSubmission, FormField, FormTemplate, FormType, Registration


def _activity(activity_id: int) -> Activity:
    return get_object_or_404(Activity.objects.select_related("status", "activity_type"), pk=activity_id)


def _tab_url(activity: Activity, tab: str, query: str = "") -> str:
    params = urlencode({"tab": tab}) + (f"&{query}" if query else "")
    return f"{reverse('activities:detail', args=[activity.pk])}?{params}"


def _card(request: HttpRequest, form: FormTemplate, text: str = "", level: str = "success", **card_options) -> HttpResponse:
    """HTMX: the form's card (with a toast); otherwise back to the Forms tab with the message."""
    activity = form.activity
    if not is_htmx(request):
        if text:
            messages.add_message(request, messages.ERROR if level == "error" else messages.SUCCESS, text)
        return redirect(_tab_url(activity, "forms"))
    context = {
        "activity": activity,
        "card": pages.form_card(activity, form.form_type, can_edit=has_permission(request.user, Permission.CREATE_ACTIVITY), **card_options),
        "can_create": has_permission(request.user, Permission.CREATE_ACTIVITY),
        "field_types": pages.FIELD_TYPE_CHOICES, "choice_types": pages.CHOICE_TYPES,
    }
    response = render(request, "registrations/_form_card.html", context)
    if text:
        response["HX-Trigger"] = json.dumps({"toast": {"level": level, "text": text}})
    return response


# --- Forms tab --------------------------------------------------------------------------------------------


@permission_required(Permission.CREATE_ACTIVITY)
@post_only_or_tab("forms")
def form_create(request: HttpRequest, activity_id: int, form_type: str) -> HttpResponse:
    """Create from the activity type's default fields. A full page load afterwards, so the header's
    registration notice and link update too."""
    activity = _activity(activity_id)
    try:
        form = services.create_form(request.user, activity, form_type)
    except services.RegistrationError as error:
        messages.error(request, str(error))
    else:
        count = form.fields.count()
        messages.success(request, f"{FormType(form_type).label} form created with {count} field{'' if count == 1 else 's'}.")
    return redirect(_tab_url(activity, "forms"))


@staff_login_required
@require_GET
def form_card(request: HttpRequest, template_id: int) -> HttpResponse:
    """HTMX: the card as it is (e.g. after "Cancel" in the field editor)."""
    return _card(request, get_object_or_404(FormTemplate.objects.select_related("activity"), pk=template_id))


@permission_required(Permission.CREATE_ACTIVITY)
@require_GET
def field_editor(request: HttpRequest, template_id: int, field_id: int | None = None) -> HttpResponse:
    """HTMX: the card with the inline editor open (a new field, or field_id)."""
    form = get_object_or_404(FormTemplate.objects.select_related("activity"), pk=template_id)
    if field_id is not None:
        get_object_or_404(FormField, pk=field_id, template=form)
    return _card(request, form, editing=field_id if field_id is not None else "new")


def _posted_field(request: HttpRequest) -> dict:
    """The editor's values. Options are the non-empty `option` inputs, only for choice types (a list:
    services.clean_options checks it and stores the JSON list)."""
    field_type = request.POST.get("field_type", "")
    values = {
        "field_label": request.POST.get("field_label", ""),
        "field_type": field_type,
        "is_required": request.POST.get("is_required") == "on",
    }
    if field_type in pages.CHOICE_TYPES:
        values["options"] = [option for option in request.POST.getlist("option") if option.strip()]
    return values


def _save_field(request: HttpRequest, form: FormTemplate, field: FormField | None) -> HttpResponse:
    values = _posted_field(request)
    try:
        if field is None:
            saved = services.add_field(request.user, form, **values)
        else:
            saved = services.update_field(request.user, field, **values)
    except ValidationError as error:
        editor = {**values, "options": values.get("options", []),
                  "errors": {key: " ".join(text) for key, text in error.message_dict.items()}}
        if not is_htmx(request):
            messages.error(request, " ".join(editor["errors"].values()))
            return redirect(_tab_url(form.activity, "forms"))
        return _card(request, form, editing=field.pk if field else "new", editor=editor)
    except services.RegistrationError as error:
        return _card(request, form, str(error), "error")
    return _card(request, form, f"“{saved.field_label}” {'added' if field is None else 'saved'}.")


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
def field_add(request: HttpRequest, template_id: int) -> HttpResponse:
    form = get_object_or_404(FormTemplate.objects.select_related("activity"), pk=template_id)
    return _save_field(request, form, None)


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
def field_update(request: HttpRequest, field_id: int) -> HttpResponse:
    field = get_object_or_404(FormField.objects.select_related("template__activity"), pk=field_id)
    return _save_field(request, field.template, field)


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
def field_remove(request: HttpRequest, field_id: int) -> HttpResponse:
    field = get_object_or_404(FormField.objects.select_related("template__activity"), pk=field_id)
    form, label = field.template, field.field_label
    try:
        services.remove_field(request.user, field)
    except services.RegistrationError as error:
        return _card(request, form, str(error), "error")
    return _card(request, form, f"“{label}” removed.")


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
def field_move(request: HttpRequest, field_id: int) -> HttpResponse:
    """POST direction=up|down. Refused on a locked form (owner decision: a locked form shows no edit
    controls at all, ASSUMPTIONS 271), although services.reorder_fields itself would allow it."""
    field = get_object_or_404(FormField.objects.select_related("template__activity"), pk=field_id)
    form = field.template
    try:
        if services.form_is_locked(form):
            raise services.RegistrationError(services.FORM_LOCKED)
        ids = list(form.fields.order_by("sort_order", "field_id").values_list("pk", flat=True))
        services.reorder_fields(request.user, form, pages.moved_order(ids, field.pk, request.POST.get("direction", "")))
    except services.RegistrationError as error:
        return _card(request, form, str(error), "error")
    return _card(request, form)


# --- Registrations tab ---------------------------------------------------------------------------------------


@staff_login_required
@require_GET
def registration_detail(request: HttpRequest, activity_id: int, registration_id: int) -> HttpResponse:
    """HTMX: one registration's details and answers (for the modal)."""
    registration = get_object_or_404(
        Registration.objects.select_related("participant_category", "startup", "evaluation_submission")
        .prefetch_related("answers__field"),
        pk=registration_id, activity_id=activity_id,
    )
    return render(request, "registrations/_registration_detail.html",
                  {"registration": registration, "answers": pages.answer_rows(registration),
                   "evaluation": pages.evaluation_detail(request.user, registration)})


@permission_required(services.INDIVIDUAL_EVALUATION_PERMISSION)
@require_GET
def evaluation_pdf(request: HttpRequest, activity_id: int, registration_id: int) -> HttpResponse:
    """One participant's submitted evaluation as PDF (ASSUMPTIONS 402): can_create_activity only (403 otherwise, also
    checked in the service); 404 when the registration is not this activity's or has not evaluated."""
    registration = get_object_or_404(Registration.objects.select_related("activity__activity_type"),
                                     pk=registration_id, activity_id=activity_id)
    try:
        file = report_services.individual_evaluation_file(request.user, registration)
    except EvaluationSubmission.DoesNotExist:
        raise Http404("This registration has no evaluation.") from None
    return activity_file_response(file)


def _attendance_request(post) -> tuple[list[str], bool]:
    """(registration IDs, mark?) from the posted attendance form. A row button sends row_action=mark:<id> or
    undo:<id> (only that row); the bulk buttons send action=mark|undo with the checked registration_ids."""
    row_action = post.get("row_action", "")
    if ":" in row_action:
        action, registration_id = row_action.split(":", 1)
        return ([registration_id] if registration_id.isdigit() else []), action == "mark"
    ids = [value for value in post.getlist("registration_ids") if value.isdigit()]
    return ids, post.get("action") == "mark"


@permission_required(Permission.CREATE_ACTIVITY)
@post_only_or_tab("registrations")
def attendance(request: HttpRequest, activity_id: int) -> HttpResponse:
    """POST method=wet_signature|email (the "Confirmed by" choice above the table, used by every mark button), then
    either row_action=mark:<id>|undo:<id> or action=mark|undo with registration_ids. `filters` (the table's
    query string) brings the member back to the same view."""
    activity = _activity(activity_id)
    back = _tab_url(activity, "registrations", pages.filters_query(request.POST.get("filters", "")))
    ids, mark = _attendance_request(request.POST)
    if not ids:
        messages.error(request, "Select at least one registration first.")
        return redirect(back)
    method = (request.POST.get("method") or None) if mark else None
    try:
        changed = services.set_attendance(request.user, activity, ids, mark, method)
    except services.RegistrationError as error:
        messages.error(request, str(error))
    else:
        people = f"{changed} registration{'' if changed == 1 else 's'}"
        if mark:
            method_label = dict(Registration.ConfirmationMethod.choices)[method].lower()
            messages.success(request, f"Marked {people} as attended ({method_label}).")
        else:
            messages.success(request, f"Attendance undone for {people}.")
    return redirect(back)


@permission_required(Permission.CREATE_ACTIVITY)
@post_only_or_tab("evaluation")
def send_links(request: HttpRequest, activity_id: int) -> HttpResponse:
    """Resend evaluation links (Evaluation tab): POST registration_id=<id> for one attendee, or nothing for everyone
    who has not answered. Same rules as services.send_evaluation_links."""
    activity = _activity(activity_id)
    raw = request.POST.get("registration_id", "")
    ids = [int(raw)] if raw.isdigit() else None
    try:
        queued = services.send_evaluation_links(request.user, activity, registration_ids=ids)
    except services.RegistrationError as error:
        messages.error(request, str(error))
    else:
        if queued:
            messages.success(request, email_sent_message(
                "They go out while qcluster is running.",
                before=f"Queued {queued} evaluation link email{'' if queued == 1 else 's'}. ",
            ))
        else:
            messages.info(request, "Nobody to send to: every attendee has answered the evaluation.")
    return redirect(_tab_url(activity, "evaluation"))


@staff_login_required
@require_GET
def attendance_sheet(request: HttpRequest, activity_id: int) -> HttpResponse:
    """A print-friendly attendance sheet (A4): name, category and an empty signature column. No email
    addresses or contact numbers."""
    activity = _activity(activity_id)
    return render(request, "registrations/attendance_sheet.html",
                  {"activity": activity, "rows": pages.attendance_sheet_rows(activity)})
