"""The Activities pages (phase F2a): list + calendar, create, edit, the activity page with its tabs, status
changes and resource persons. Thin views: every rule is in activities.services (and registrations.services
for the registration state); activities/pages.py prepares what the templates show."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from accounts.permissions import Permission, has_permission, is_htmx, permission_required, staff_login_required
from config.links import activity_tab_url, post_only_or_tab
from certificates import pages as certificate_pages
from documents import pages as document_pages
from documents import services as document_services
from registrations import pages as registration_pages
from registrations import services as registration_services
from reports import services as report_services

from . import attention, deletion, pages, services, share
from .forms import ActivityPageFormWithSignatories as ActivityPageForm
from .models import Activity, ActivityStatus, ActivityType, ParticipantCategory, ResourcePerson


def _can_create(request: HttpRequest) -> bool:
    return has_permission(request.user, Permission.CREATE_ACTIVITY)


def _detail_url(activity: Activity, tab: str = "") -> str:
    url = reverse("activities:detail", args=[activity.pk])
    return f"{url}?tab={tab}" if tab and tab != pages.DEFAULT_TAB else url


# --- List and calendar -------------------------------------------------------------------------------


@staff_login_required
@require_GET
def activity_list(request: HttpRequest) -> HttpResponse:
    """GET q, activity_type, status, participant_category, date_from, date_to, sort, preset (from a card dialog's
    "Show all in the list"), page, view=list|calendar, month=YYYY-MM. An HTMX request (a filter change, a page or
    month link) gets only the results part, plus the hidden preset out of band. The automatic status moves run
    first (statuses only, never emails), so the list never shows a status that is already out of date."""
    services.apply_automatic_statuses()
    filters = pages.parse_list_filters(request.GET)
    context = {"page_title": "Activities", "filters": filters, "q": filters.q, "can_create": _can_create(request)}
    if filters.view == pages.CALENDAR:
        context["calendar"] = pages.month_grid(filters.month)
    elif filters.grouped:  # the default: sections (services.grouped_activities); only Past is paginated
        groups = services.grouped_activities(filters.for_service(), page=filters.page)
        context.update(sections=pages.grouped_sections(groups, context["can_create"]),
                       grouped_total=sum(group.total for group in groups), page_query=filters.query(),
                       clear_preset_query=filters.query(preset=""))
    else:
        result = services.list_activities(filters.for_service(), page=filters.page)
        context.update(result=result, rows=pages.list_rows(result.items), page_query=filters.query(),
                       clear_preset_query=filters.query(preset=""))
    if is_htmx(request):
        context["oob"] = filters.view == pages.LIST
        return render(request, "activities/_results.html", context)
    context.update(
        cards=pages.stat_cards(services.activity_stats()),
        sort_choices=pages.SORT_CHOICES,
        activity_types=ActivityType.objects.order_by("name"),
        statuses=ActivityStatus.objects.order_by("pk"),
        categories=ParticipantCategory.objects.order_by("name"),
    )
    return render(request, "activities/list.html", context)


@staff_login_required
@require_GET
def card_dialog(request: HttpRequest, preset: str) -> HttpResponse:
    """A list card's dialog (ASSUMPTIONS 341): its activities from the card's own query, loaded with HTMX into
    #card-dialog. Without JavaScript the card is a link to the list with the preset, so this URL redirects there."""
    if preset not in services.LIST_PRESETS:
        raise Http404("Unknown list card.")
    if not is_htmx(request):
        return redirect(pages.preset_list_url(preset))
    return render(request, "activities/_card_dialog.html", {"dialog": pages.card_dialog(preset)})


# --- Create and edit -----------------------------------------------------------------------------------


def _put_errors_on_fields(form: ActivityPageForm, error: ValidationError) -> None:
    """A service ValidationError({field: message}) goes under its field; anything else on top of the form."""
    for name, messages_ in error.message_dict.items():
        form.add_error(name if name in form.fields else None, messages_)


def _initial(activity: Activity) -> dict:
    values = services.activity_values(activity)
    values["participant_categories"] = [category.pk for category in values["participant_categories"]]
    values["activity_type"] = activity.activity_type_id
    return values


@permission_required(Permission.CREATE_ACTIVITY)
@require_http_methods(["GET", "POST"])
def activity_create(request: HttpRequest) -> HttpResponse:
    """The activity, its signatories (pre-filled with the defaults, ASSUMPTIONS 365), its required Activity Design and
    Request Letter (ASSUMPTIONS 405) and its optional documents are saved by one service call, all or nothing
    (documents.services.create_activity_with_documents, ASSUMPTIONS 327). The required rows come first, so the
    service's row errors (by position) map back onto `required + rows`."""
    form = ActivityPageForm(request.POST or None, **pages.signatory_form_options(request.user))
    required = document_pages.required_document_rows(request)
    rows = document_pages.new_document_rows(request) if request.method == "POST" else []
    if request.method == "POST":
        all_rows = [*required, *rows]
        uploads = document_pages.new_documents_for_service(all_rows)
        if form.is_valid():
            signatories = form.signatory_choices()
            try:
                activity = document_services.create_activity_with_documents(request.user, form.cleaned_data, uploads,
                                                                            signatories=signatories)
            except document_services.NewActivityError as error:
                if error.activity_error:
                    _put_errors_on_fields(form, error.activity_error)
                document_pages.put_row_errors(all_rows, error.row_errors)
                if error.missing_types:  # only when a type became required a moment ago (the form has a row per type)
                    form.add_error(None, f"Upload these documents too: {', '.join(error.missing_types)}.")
            except ValidationError as error:
                _put_errors_on_fields(form, error)
            except services.ActivityError as error:
                form.add_error(None, str(error))
            else:
                count = len(uploads)
                extra = f" with {count} document{'s' if count != 1 else ''} waiting for review" if count else ""
                messages.success(request, f'"{activity.title}" was created as Draft{extra}.')
                return redirect(_detail_url(activity, "documents" if count else ""))
        else:  # show the rows' problems together with the fields' ones
            document_pages.put_row_errors(all_rows, document_services.check_new_documents(uploads))
        if any(row.errors for row in all_rows):
            form.add_error(None, "Please correct the documents marked below.")
    context = {"page_title": "New activity", "form": form, "activity": None, "docs": document_pages.new_documents_context(rows, required),
               "signatories": pages.signatory_form_section(form, request.user)}
    return render(request, "activities/form.html", context)


@permission_required(Permission.CREATE_ACTIVITY)
@require_http_methods(["GET", "POST"])
def activity_edit(request: HttpRequest, activity_id: int) -> HttpResponse:
    """A Conducted activity is shown read-only (the service refuses edits; a POST gets its message)."""
    activity = get_object_or_404(Activity.objects.select_related("status", "activity_type"), pk=activity_id)
    form = ActivityPageForm(request.POST or None, initial=_initial(activity), activity=activity,
                            **pages.signatory_form_options(request.user, activity))
    if request.method == "POST" and form.is_valid():
        signatories = form.signatory_choices()
        try:
            services.update_activity(request.user, activity, pages.keep_stored_minutes(form.cleaned_data, activity),
                                     signatories=signatories)
        except ValidationError as error:
            _put_errors_on_fields(form, error)
        except services.ActivityError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Changes saved.")
            return redirect(_detail_url(activity))
    context = {"page_title": f"Edit {activity.title}", "form": form, "activity": activity,
               "signatories": pages.signatory_form_section(form, request.user)}
    if activity.status.name == ActivityStatus.CONDUCTED:
        context["detail_rows"] = pages.detail_rows(services.get_activity_detail(activity))
        return render(request, "activities/read_only.html", context)
    return render(request, "activities/form.html", context)


# --- The activity page -------------------------------------------------------------------------------------


@staff_login_required
@require_GET
def activity_detail(request: HttpRequest, activity_id: int) -> HttpResponse:
    """GET ?tab=overview|history|forms|registrations|evaluation|documents|certificates (&page= for History;
    &q, attended, category, page for Registrations; &type= for Documents). An HTMX request (a tab, a page, a filter)
    gets only the tab; the Registrations filters pick their table out of it (hx-select). The automatic status
    moves run first for this activity (statuses only, never emails)."""
    activity = get_object_or_404(Activity.objects.select_related("status", "activity_type", "created_by"), pk=activity_id)
    services.refresh_status(activity)
    return render_activity_page(request, activity, pages.tab_key(request.GET.get("tab")))


def render_activity_page(request: HttpRequest, activity: Activity, tab: str, extra: dict | None = None,
                         status: int = 200) -> HttpResponse:
    """The activity page (or, for HTMX, only its tab) showing `tab`. `extra` adds to the tab's context: the
    Documents upload form's errors and the Certificates bulk refusals (phase F4) re-render the page with them."""
    detail = services.get_activity_detail(activity)
    can_create = _can_create(request)
    context = {
        "page_title": activity.title, "activity": activity, "detail": detail, "tab": tab, "tabs": pages.tabs(tab),
        # Every tab link starts with the activity's own address, never "?tab=" alone (ASSUMPTIONS 362).
        "activity_url": reverse("activities:detail", args=[activity.pk]),
        "can_create": can_create,
        "can_edit": can_create and activity.status.name != ActivityStatus.CONDUCTED,
        "can_delete": has_permission(request.user, Permission.DELETE_ACTIVITIES),  # the "⋯" menu (ASSUMPTIONS 389)
        "registration": pages.registration_summary(activity),
        "attention": (items := attention.activity_attention(activity)),
        "attention_count": attention.warning_count(items),  # informational items not counted (ASSUMPTIONS 364)
        # The public address (SITE_URL), never the Host header of this request.
        "register_url": registration_services.registration_page_url(activity),
    }
    if tab == "overview":
        context["detail_rows"] = pages.detail_rows(detail)
        context["share"] = share.share_card(activity, context["registration"])
        context["by_category"] = pages.category_donut(
            report_services.activity_participants_by_category(request.user, activity))
    elif tab == "history":
        context["history"], context["history_rows"] = pages.history(activity, request.GET.get("page", 1))
    elif tab == "forms":
        context.update(registration_pages.forms_tab(activity, can_create), field_types=registration_pages.FIELD_TYPE_CHOICES,
                       choice_types=registration_pages.CHOICE_TYPES)
    elif tab == "registrations":
        context.update(registration_pages.registrations_tab(request.user, activity, request.GET))
    elif tab == "evaluation":
        context.update(registration_pages.evaluation_tab(request.user, activity, request.GET))
    elif tab == "documents":
        context.update(document_pages.documents_tab(request, activity, (extra or {}).get("upload")))
    elif tab == "certificates":
        context.update(certificate_pages.certificates_tab(request, activity, (extra or {}).get("problems"),
                                                          (extra or {}).get("text_form")))
    if is_htmx(request):
        return render(request, "activities/_tab.html", context, status=status)
    if request.path != context["activity_url"]:  # re-shown after a POST (form errors): fix the address bar
        context["canonical_url"] = activity_tab_url(activity.pk, tab)
    return render(request, "activities/detail.html", context, status=status)


@staff_login_required
@require_GET
def qr_code(request: HttpRequest, activity_id: int, kind: str) -> HttpResponse:
    """The QR code (PNG) of a link on the Share card: kind "registration" or "evaluation" (activities.share.qr_url;
    anything the card does not show, Draft included, is 404). Any active staff member. ?download=1 sends it as a
    file for posters."""
    activity = get_object_or_404(Activity.objects.select_related("status"), pk=activity_id)
    url = share.qr_url(activity, kind)
    if url is None:
        raise Http404("No QR code for this activity.")
    response = HttpResponse(share.qr_png(url), content_type="image/png")
    if request.GET.get("download") == "1":
        response["Content-Disposition"] = f'attachment; filename="activity-{activity.pk}-{kind}-qr.png"'
    response["Cache-Control"] = "private, no-cache"  # the link (and so the image) can change with the status
    return response


@permission_required(Permission.CREATE_ACTIVITY)
@post_only_or_tab("overview")
def activity_change_status(request: HttpRequest, activity_id: int) -> HttpResponse:
    """POST status=<name> (&next=list: back to the Activities list, from a "Needs follow-up" card's "Mark as
    Conducted"; any other value goes to the activity page). Same messages as the Django admin's "Change status"."""
    activity = get_object_or_404(Activity.objects.select_related("status"), pk=activity_id)
    old_status, new_status = activity.status.name, request.POST.get("status", "")
    try:
        services.change_status(request.user, activity, new_status)
    except services.ActivityError as error:
        messages.error(request, f"Status not changed: {error}")
    else:
        messages.success(request, f'Status of "{activity.title}" changed from {old_status} to {new_status}.')
    if request.POST.get("next") == "list":  # a fixed name, never a URL from the request (no open redirect)
        return redirect("activities:list")
    return redirect(_detail_url(activity))


# --- Deleting (ASSUMPTIONS 389) ----------------------------------------------------------------------------


def _delete_context(activity: Activity, typed_title: str = "", error: str = "") -> dict:
    summary = deletion.deletion_summary(activity)
    return {
        "activity": activity,
        "refused": deletion.deletion_refused_reason(activity, summary),
        "lines": deletion.what_will_be_deleted(summary),
        "nothing_else": not deletion.what_will_be_deleted(summary),
        "typed_title": typed_title,
        "error": error,
    }


@permission_required(Permission.DELETE_ACTIVITIES)
@require_http_methods(["GET", "POST"])
def activity_delete(request: HttpRequest, activity_id: int) -> HttpResponse:
    """GET: what deleting the activity would remove, or why it is refused (HTMX: the "⋯" menu's dialog; otherwise a
    page). POST title=<typed title>: deletion.delete_activity, then the Activities list with a toast. A wrong title or
    a refusal shows the page again with the reason."""
    activity = get_object_or_404(Activity.objects.select_related("status", "activity_type"), pk=activity_id)
    if request.method == "GET":
        template = "activities/_delete_dialog.html" if is_htmx(request) else "activities/delete_page.html"
        return render(request, template, {"page_title": f"Delete {activity.title}", **_delete_context(activity)})
    typed = request.POST.get("title", "")
    try:
        record = deletion.delete_activity(request.user, activity, typed)
    except ValidationError as error:
        message = " ".join(error.message_dict.get("title", [])) if hasattr(error, "error_dict") else " ".join(error.messages)
        context = {"page_title": f"Delete {activity.title}", **_delete_context(activity, typed, message)}
        return render(request, "activities/delete_page.html", context, status=400)
    messages.success(request, f"“{record.activity_title}” was deleted. A record of it is kept in the Admin Console "
                              "(Deleted activities).")
    return redirect("activities:list")


# --- Resource persons --------------------------------------------------------------------------------------


@permission_required(Permission.CREATE_ACTIVITY)
@post_only_or_tab("overview")
def resource_person_add(request: HttpRequest, activity_id: int) -> HttpResponse:
    """POST mode=existing (resource_person, topic) or mode=new (name, email, contact_number, topic)."""
    activity = get_object_or_404(Activity, pk=activity_id)
    topic = request.POST.get("topic", "")
    try:
        with transaction.atomic():  # a new person is kept only if linking them works too
            if request.POST.get("mode") == "new":
                person = services.create_resource_person(
                    request.user, request.POST.get("name", ""), request.POST.get("email", ""), request.POST.get("contact_number", "")
                )
            else:
                raw = request.POST.get("resource_person", "")
                person = ResourcePerson.objects.filter(pk=raw).first() if raw.isdigit() else None
                if person is None:
                    raise services.ActivityError("Choose a resource person from the list.")
            services.add_resource_person(request.user, activity, person, topic)
    except ValidationError as error:
        messages.error(request, " ".join(f"{name.replace('_', ' ').capitalize()}: {' '.join(text)}" for name, text in error.message_dict.items()))
    except services.ActivityError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{person.name} was added as a resource person.")
    return redirect(f"{_detail_url(activity)}#resource-persons")


@permission_required(Permission.CREATE_ACTIVITY)
@post_only_or_tab("overview")
def resource_person_remove(request: HttpRequest, activity_id: int, resource_person_id: int) -> HttpResponse:
    activity = get_object_or_404(Activity, pk=activity_id)
    person = get_object_or_404(ResourcePerson, pk=resource_person_id)
    try:
        services.remove_resource_person(request.user, activity, person)
    except services.ActivityError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{person.name} was removed as a resource person.")
    return redirect(f"{_detail_url(activity)}#resource-persons")


@permission_required(Permission.CREATE_ACTIVITY)
@require_GET
def resource_person_options(request: HttpRequest) -> HttpResponse:
    """HTMX: the pick list for "add an existing person" (GET q=)."""
    people = services.search_resource_persons(request.GET.get("q", ""), limit=20)
    return render(request, "activities/_resource_person_options.html", {"people": people, "q": request.GET.get("q", "")})
