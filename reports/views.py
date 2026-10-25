"""JSON API and the download view for the dashboard and reports. Views are thin: parse input,
check access, call services.

- Any active staff member: dashboard numbers, report options, list and detail, download.
- can_create_activity (pending TBI): request a report (checked in services.request_report).
Report files are never served from /media/: only through download_report, always as attachments.
"""

import json

from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from accounts.permissions import is_htmx, staff_login_required
from activities.models import Activity
from config.json import JsonResponse  # date-times in Manila time

from . import services
from .content import range_in_words
from .dashboard_page import build_dashboard
from .models import GeneratedReport


def _errors(error: ValidationError) -> JsonResponse:
    errors = error.message_dict if hasattr(error, "error_dict") else {"__all__": error.messages}
    return JsonResponse({"errors": errors}, status=400)


def report_json(report: GeneratedReport, state: str, detail: bool = False) -> dict:
    data = {
        "report_id": report.pk,
        "report_type": report.report_type,
        "report_type_label": services.REPORT_TYPES[report.report_type].label if report.report_type in services.REPORT_TYPES else report.report_type,
        "included_sections": services.sections_of(report),
        "date_range_start": report.date_range_start,
        "date_range_end": report.date_range_end,
        "date_range_text": range_in_words(report.date_range_start, report.date_range_end),
        "activity_type": {"activity_type_id": report.activity_type_id, "name": report.activity_type.name} if report.activity_type else None,
        "format": report.format,
        "state": state,
        "generated_by": {"member_id": report.generated_by_id, "name": report.generated_by.name},
        "generated_at": report.generated_at,
        "download_url": reverse("reports:download", args=[report.pk]) if state == services.READY else None,
    }
    if detail:
        data["ai_prompt"] = report.ai_prompt  # the exact prompt sent to Gemini (figures only), or null
    return data


@staff_login_required
@require_GET
def dashboard_page(request: HttpRequest) -> HttpResponse:
    """The staff dashboard (/). GET range=this_month|this_quarter|this_year|last_year|custom,
    start, end (YYYY-MM-DD, for custom) and activity_type. The filter form sends HTMX requests: they
    get only the part below the filters (dashboard/_body.html); the URL keeps the filters."""
    page = build_dashboard(request.user, request.GET)
    template = "dashboard/_body.html" if is_htmx(request) else "dashboard/index.html"
    return render(request, template, {"page_title": "Dashboard", "page": page})


@staff_login_required
@require_GET
def dashboard_api(request: HttpRequest) -> JsonResponse:
    """GET ?start=YYYY-MM-DD&end=YYYY-MM-DD&activity_type=<id> (default: the current year, Manila)."""
    try:
        return JsonResponse(services.dashboard(request.user, request.GET))
    except ValidationError as error:
        return _errors(error)


@staff_login_required
@require_GET
def activity_evaluation_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """GET: one activity's evaluation results: registered, attended, evaluations, response rate and
    the average of each rating question (same rules as the reports; no comments or text answers)."""
    activity = get_object_or_404(Activity, pk=activity_id)
    return JsonResponse(services.activity_evaluation(request.user, activity))


@staff_login_required
@require_GET
def options_api(request: HttpRequest) -> JsonResponse:
    return JsonResponse(services.report_options())


@staff_login_required
@require_http_methods(["GET", "POST"])
def reports_api(request: HttpRequest) -> JsonResponse:
    """GET: list (?report_type=&format=&page=). POST: request a new report (can_create_activity)."""
    if request.method == "POST":
        try:
            data = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
        if not isinstance(data, dict):
            return JsonResponse({"error": "Request body must be a JSON object."}, status=400)
        try:
            report = services.request_report(request.user, data)
        except ValidationError as error:
            return _errors(error)
        return JsonResponse({"report": report_json(report, services.PENDING, detail=True)}, status=202)

    result = services.list_reports(
        {"report_type": request.GET.get("report_type"), "format": request.GET.get("format")}, request.GET.get("page")
    )
    failed = services.failed_report_ids([report.pk for report in result.items if not report.file_path])
    return JsonResponse({
        "reports": [report_json(report, services.report_state(report, failed)) for report in result.items],
        "total": result.total,
        "page": result.page,
        "num_pages": result.num_pages,
    })


@staff_login_required
@require_GET
def report_detail_api(request: HttpRequest, report_id: int) -> JsonResponse:
    report = get_object_or_404(GeneratedReport.objects.select_related("generated_by", "activity_type"), pk=report_id)
    return JsonResponse({"report": report_json(report, services.report_state(report), detail=True)})


@staff_login_required
@require_GET
def download_report(request: HttpRequest, report_id: int) -> FileResponse:
    """Any active staff member; always an attachment. 404 until the file is ready."""
    report = get_object_or_404(GeneratedReport, pk=report_id)
    if not report.file_path:
        raise Http404("The report is not ready yet.")
    try:
        handle = report.file_path.open("rb")
    except (FileNotFoundError, ValueError) as error:
        raise Http404("The report file is missing.") from error
    extension = services.EXTENSIONS[report.format]
    response = FileResponse(
        handle, as_attachment=True, filename=services.download_name(report), content_type=services.CONTENT_TYPES[extension]
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
