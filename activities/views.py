"""JSON API for activities. Views are thin: parse input, check permission, call services.

Reading needs any signed-in staff member; every change needs can_create_activity
(checked here with require_permission and again inside the service).

The audit log is append-only: there is deliberately no endpoint that changes or deletes it.
"""

import json
from collections.abc import Callable
from datetime import date
from functools import wraps

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from config.json import JsonResponse  # date-times in Manila time

from accounts.permissions import Permission, permission_required, require_permission, staff_login_required

from . import services
from .forms import parse_activity_data
from .models import Activity, ResourcePerson


class BadRequest(Exception):
    """Invalid query parameters or request body."""


def api_errors(view: Callable) -> Callable:
    """Turn service and input errors into 400 responses with a clear message."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> JsonResponse:
        try:
            return view(request, *args, **kwargs)
        except ValidationError as error:
            errors = error.message_dict if hasattr(error, "error_dict") else {"__all__": error.messages}
            return JsonResponse({"errors": errors}, status=400)
        except (services.ActivityError, BadRequest) as error:
            return JsonResponse({"error": str(error)}, status=400)

    return wrapper


def _json_body(request: HttpRequest) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError as error:
        raise BadRequest("Request body must be valid JSON.") from error
    if not isinstance(data, dict):
        raise BadRequest("Request body must be a JSON object.")
    return data


def _date_param(request: HttpRequest, name: str, required: bool = False) -> date | None:
    value = request.GET.get(name)
    if not value:
        if required:
            raise BadRequest(f"{name} is required (YYYY-MM-DD).")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise BadRequest(f"{name} must be a date like 2026-10-15.") from error


def _int_param(request: HttpRequest, name: str) -> int | None:
    value = request.GET.get(name)
    if not value:
        return None
    if not value.isdigit():
        raise BadRequest(f"{name} must be a number.")
    return int(value)


def _activity_row(activity: Activity) -> dict:
    """One row of the Activities list."""
    registered = getattr(activity, "registered", None)
    if registered is None:
        registered = services.registered_count(activity)
    return {
        "activity_id": activity.pk,
        "title": activity.title,
        "status": activity.status.name,
        "activity_type_id": activity.activity_type_id,
        "activity_type": activity.activity_type.name,
        "participant_category_ids": sorted(category.pk for category in activity.participant_categories.all()),
        "participant_categories": sorted(category.name for category in activity.participant_categories.all()),
        "start_date": activity.start_date,
        "end_date": activity.end_date,
        "venue": activity.venue,
        "registered_count": registered,
        "max_slots": activity.max_slots,
        "slots": f"{registered}/{activity.max_slots}",
    }


def _page_json(result: services.PageResult, key: str, rows: list) -> dict:
    return {key: rows, "total": result.total, "page": result.page, "num_pages": result.num_pages}


# --- List, create, stats, calendar -------------------------------------------------------


@staff_login_required
@require_http_methods(["GET", "POST"])
@api_errors
def activities_api(request: HttpRequest) -> JsonResponse:
    """GET: list with search/filters/sort/page. POST: create a Draft activity."""
    if request.method == "POST":
        require_permission(request.user, Permission.CREATE_ACTIVITY)
        activity = services.create_activity(request.user, parse_activity_data(_json_body(request)))
        return JsonResponse({"activity": services.get_activity_detail(activity)}, status=201)

    filters = {
        "search": request.GET.get("search", ""),
        "activity_type": _int_param(request, "activity_type"),
        "status": _int_param(request, "status"),
        "participant_category": _int_param(request, "participant_category"),
        "date_from": _date_param(request, "date_from"),
        "date_to": _date_param(request, "date_to"),
        "sort": request.GET.get("sort"),
    }
    result = services.list_activities(filters, page=request.GET.get("page", 1))
    return JsonResponse(_page_json(result, "activities", [_activity_row(a) for a in result.items]))


@staff_login_required
@require_GET
def stats_api(request: HttpRequest) -> JsonResponse:
    return JsonResponse(services.activity_stats())


@staff_login_required
@require_GET
@api_errors
def calendar_api(request: HttpRequest) -> JsonResponse:
    """GET ?start=YYYY-MM-DD&end=YYYY-MM-DD"""
    start, end = _date_param(request, "start", required=True), _date_param(request, "end", required=True)
    events = [
        {
            "activity_id": a.pk,
            "title": a.title,
            "start_date": a.start_date,
            "end_date": a.end_date,
            "status": a.status.name,
            "activity_type": a.activity_type.name,
        }
        for a in services.activities_between(start, end)
    ]
    return JsonResponse({"activities": events})


# --- One activity ------------------------------------------------------------------------


@staff_login_required
@require_GET
def detail_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    activity = get_object_or_404(Activity, pk=activity_id)
    return JsonResponse({"activity": services.get_activity_detail(activity)})


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def update_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """POST only the fields to change, e.g. {"venue": "Room 101"}."""
    activity = get_object_or_404(Activity, pk=activity_id)
    services.update_activity(request.user, activity, parse_activity_data(_json_body(request), partial=True))
    return JsonResponse({"activity": services.get_activity_detail(activity)})


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def status_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """POST {"status": "Scheduled"}"""
    activity = get_object_or_404(Activity, pk=activity_id)
    services.change_status(request.user, activity, str(_json_body(request).get("status", "")))
    return JsonResponse({"activity": services.get_activity_detail(activity)})


@staff_login_required
@require_GET
@api_errors
def audit_log_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """GET ?field=Venue&date_from=&date_to=&page="""
    activity = get_object_or_404(Activity, pk=activity_id)
    result = services.get_audit_log(
        activity,
        field=request.GET.get("field") or None,
        date_from=_date_param(request, "date_from"),
        date_to=_date_param(request, "date_to"),
        page=request.GET.get("page", 1),
    )
    rows = [
        {
            "field_changed": row.field_changed,
            "old_value": row.old_value,
            "new_value": row.new_value,
            "changed_by": row.changed_by.name,
            "changed_at": row.changed_at,
        }
        for row in result.items
    ]
    return JsonResponse(_page_json(result, "audit_log", rows))


# --- Resource persons ----------------------------------------------------------------------


def _resource_person_json(person: ResourcePerson) -> dict:
    return {
        "resource_person_id": person.pk,
        "name": person.name,
        "email": person.email,
        "contact_number": person.contact_number,
    }


@staff_login_required
@require_http_methods(["GET", "POST"])
@api_errors
def resource_persons_api(request: HttpRequest) -> JsonResponse:
    """GET ?search=: pick list. POST {name, email, contact_number}: add a new person."""
    if request.method == "POST":
        require_permission(request.user, Permission.CREATE_ACTIVITY)
        data = _json_body(request)
        person = services.create_resource_person(
            request.user, str(data.get("name", "")), str(data.get("email", "")), str(data.get("contact_number", ""))
        )
        return JsonResponse({"resource_person": _resource_person_json(person)}, status=201)

    people = ResourcePerson.objects.order_by("name")
    if search := request.GET.get("search", "").strip():
        people = people.filter(name__icontains=search)
    return JsonResponse({"resource_persons": [_resource_person_json(p) for p in people[:100]]})


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def add_resource_person_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """POST {"resource_person_id": 3, "topic": "Pitching"}"""
    activity = get_object_or_404(Activity, pk=activity_id)
    data = _json_body(request)
    person = ResourcePerson.objects.filter(pk=data.get("resource_person_id")).first()
    if person is None:
        raise BadRequest("Unknown resource_person_id.")
    services.add_resource_person(request.user, activity, person, str(data.get("topic", "")))
    return JsonResponse({"activity": services.get_activity_detail(activity)}, status=201)


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
@api_errors
def remove_resource_person_api(request: HttpRequest, activity_id: int, resource_person_id: int) -> JsonResponse:
    activity = get_object_or_404(Activity, pk=activity_id)
    person = get_object_or_404(ResourcePerson, pk=resource_person_id)
    services.remove_resource_person(request.user, activity, person)
    return JsonResponse({"activity": services.get_activity_detail(activity)})

