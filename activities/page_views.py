"""The Activities pages (phase F2a): list + calendar, create, edit, the activity page with its tabs, status
changes and resource persons. Thin views: every rule is in activities.services (and registrations.services
for the registration state); activities/pages.py prepares what the templates show."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.permissions import Permission, has_permission, is_htmx, permission_required, staff_login_required
from registrations import pages as registration_pages

from . import pages, services
from .forms import ActivityPageForm
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
    """GET q, activity_type, status, participant_category, date_from, date_to, sort, page, view=list|calendar,
    month=YYYY-MM. An HTMX request (a filter change, a page or month link) gets only the results part."""
    filters = pages.parse_list_filters(request.GET)
    context = {"page_title": "Activities", "filters": filters, "q": filters.q, "can_create": _can_create(request)}
    if filters.view == pages.CALENDAR:
        context["calendar"] = pages.month_grid(filters.month)
    else:
        result = services.list_activities(filters.for_service(), page=filters.page)
        context.update(result=result, rows=pages.list_rows(result.items), page_query=filters.query())
    if is_htmx(request):
        return render(request, "activities/_results.html", context)
    context.update(
        stats=services.activity_stats(),
        sort_choices=pages.SORT_CHOICES,
        activity_types=ActivityType.objects.order_by("name"),
        statuses=ActivityStatus.objects.order_by("pk"),
        categories=ParticipantCategory.objects.order_by("name"),
    )
    return render(request, "activities/list.html", context)


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
    form = ActivityPageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            activity = services.create_activity(request.user, form.cleaned_data)
        except ValidationError as error:
            _put_errors_on_fields(form, error)
        except services.ActivityError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, f'"{activity.title}" was created as Draft.')
            return redirect(_detail_url(activity))
    return render(request, "activities/form.html", {"page_title": "New activity", "form": form, "activity": None})


@permission_required(Permission.CREATE_ACTIVITY)
@require_http_methods(["GET", "POST"])
def activity_edit(request: HttpRequest, activity_id: int) -> HttpResponse:
    """A Conducted activity is shown read-only (the service refuses edits; a POST gets its message)."""
    activity = get_object_or_404(Activity.objects.select_related("status", "activity_type"), pk=activity_id)
    form = ActivityPageForm(request.POST or None, initial=_initial(activity), activity=activity)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_activity(request.user, activity, pages.keep_stored_minutes(form.cleaned_data, activity))
        except ValidationError as error:
            _put_errors_on_fields(form, error)
        except services.ActivityError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Changes saved.")
            return redirect(_detail_url(activity))
    context = {"page_title": f"Edit {activity.title}", "form": form, "activity": activity}
    if activity.status.name == ActivityStatus.CONDUCTED:
        context["detail_rows"] = pages.detail_rows(services.get_activity_detail(activity))
        return render(request, "activities/read_only.html", context)
    return render(request, "activities/form.html", context)


# --- The activity page -------------------------------------------------------------------------------------


@staff_login_required
@require_GET
def activity_detail(request: HttpRequest, activity_id: int) -> HttpResponse:
    """GET ?tab=overview|history|forms|registrations|evaluation|documents|certificates (&page= for History;
    &q, attended, category, page for Registrations). An HTMX request (a tab, a page, a filter) gets only the tab;
    the Registrations filters pick their table out of it (hx-select)."""
    activity = get_object_or_404(Activity.objects.select_related("status", "activity_type", "created_by"), pk=activity_id)
    tab = pages.tab_key(request.GET.get("tab"))
    detail = services.get_activity_detail(activity)
    can_create = _can_create(request)
    context = {
        "page_title": activity.title, "activity": activity, "detail": detail, "tab": tab, "tabs": pages.tabs(tab),
        "tab_phase": pages.tab_phase(tab), "can_create": can_create,
        "can_edit": can_create and activity.status.name != ActivityStatus.CONDUCTED,
        "registration": pages.registration_summary(activity),
        "register_url": request.build_absolute_uri(reverse("register", args=[activity.pk])),
    }
    if tab == "overview":
        context["detail_rows"] = pages.detail_rows(detail)
    elif tab == "history":
        context["history"], context["history_rows"] = pages.history(activity, request.GET.get("page", 1))
    elif tab == "forms":
        context.update(registration_pages.forms_tab(activity, can_create), field_types=registration_pages.FIELD_TYPE_CHOICES,
                       choice_types=registration_pages.CHOICE_TYPES)
    elif tab == "registrations":
        context.update(registration_pages.registrations_tab(request.user, activity, request.GET))
    elif tab == "evaluation":
        context.update(registration_pages.evaluation_tab(request.user, activity))
    if is_htmx(request):
        return render(request, "activities/_tab.html", context)
    return render(request, "activities/detail.html", context)


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
def activity_change_status(request: HttpRequest, activity_id: int) -> HttpResponse:
    """POST status=<name>. Same messages as the Django admin's "Change status"."""
    activity = get_object_or_404(Activity.objects.select_related("status"), pk=activity_id)
    old_status, new_status = activity.status.name, request.POST.get("status", "")
    try:
        services.change_status(request.user, activity, new_status)
    except services.ActivityError as error:
        messages.error(request, f"Status not changed: {error}")
    else:
        messages.success(request, f'Status of "{activity.title}" changed from {old_status} to {new_status}.')
    return redirect(_detail_url(activity))


# --- Resource persons --------------------------------------------------------------------------------------


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
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
@require_POST
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
