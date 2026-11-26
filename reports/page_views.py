"""The ONE Reports page (phase F5; ASSUMPTIONS 461-462): /reports/?kind=<kind>&activity=<id> with one list of what to
make (figures reports and the report generator's kinds; for one activity also the attendance forms) and one list of
past reports. Thin views: the rules are in reports.services (request_report checks can_create_activity and the input;
report_state decides pending, ready or failed) and reports.generator; reports/hub.py and reports/pages.py prepare
what the templates show. /reports/generator/ redirects here (reports.generator_views).

Any active staff member lists and downloads reports; only can_create_activity sees the form and may post it. A
pending row asks for itself again (HTMX polling, reports:row) until it is ready or failed.
"""

import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from approvals import pages as approval_pages  # the Assign dialog (ASSUMPTIONS 437)
from accounts.permissions import is_htmx, staff_login_required
from activities.models import Activity

from . import hub, pages, services
from .models import GeneratedReport


def chosen_activity(request: HttpRequest) -> Activity | None:
    raw = request.GET.get("activity", "") or request.POST.get("for_activity", "")
    return Activity.objects.select_related("status").filter(pk=raw).first() if raw.isdigit() else None


def render_hub(request: HttpRequest, kind: str | None = None, activity: Activity | None = None, *, figures_form=None,
               generator_form=None, status: int = 200) -> HttpResponse:
    """The one Reports page for `kind` (default: the figures Accomplishment report; for one activity, the Activity
    Report)."""
    kind = hub.choose_kind(kind if kind is not None else request.GET.get("kind"), activity)
    context = {**hub.page_context(request, kind, activity, figures_form=figures_form, generator_form=generator_form),
               "assign": approval_pages.assign_panel(request.user, report=True)}
    return render(request, "reports/list.html", context, status=status)


def _page(request: HttpRequest, form: pages.ReportForm, status: int = 200) -> HttpResponse:
    return render_hub(request, f"{hub.FIGURES_PREFIX}{form.report_type}", figures_form=form, status=status)


@staff_login_required
@require_http_methods(["GET", "POST"])
def report_list(request: HttpRequest) -> HttpResponse:
    """GET: the form (can_create_activity only) and the history (?page=). An HTMX request (the pager) gets only the
    history. POST: request a report (services.request_report, which checks the permission and every value);
    errors come back under their fields (400)."""
    if request.method == "GET":
        activity = chosen_activity(request)
        if is_htmx(request):  # the pager of the past reports
            return render(request, "reports/_history.html", {"poll_seconds": pages.POLL_SECONDS, "activity": activity,
                                                             **hub.past(request.user, activity, request.GET.get("page", "1"))})
        return render_hub(request, activity=activity)

    form = pages.form_from_post(request.POST, pages.today())
    from .generator import NewFile

    files = [NewFile(f, "document") for f in request.FILES.getlist("report_files")]
    try:
        report = services.request_report(request.user, form.service_data(), files=files)  # PermissionDenied -> 403
    except ValidationError as error:
        form.errors = pages.flat_errors(error.message_dict)
        return _page(request, form, status=400)
    messages.success(request, f"Report {report.pk} was requested. It appears below as Pending until it is made.")
    return redirect("reports:list")


@staff_login_required
@require_POST
def attendance_form(request: HttpRequest) -> HttpResponse:
    """Step 2 of "Attendance form" / "Confirmation of Attendance" on the general Reports page (ASSUMPTIONS 467): the
    activity chosen in the picker. The same rule as the Documents tab's buttons
    (registrations.attendance_forms.generate_form: can_create_activity or the activity's assignment, PermissionDenied ->
    403; refused_reason). Back to the Reports page with the kind and the activity chosen, and a message."""
    from certificates.pdf import ImageUnreadable, NotAPdf
    from registrations import attendance_forms

    raw = request.POST.get("activity", "")
    activity = get_object_or_404(Activity.objects.select_related("status"), pk=raw if raw.isdigit() else 0)
    kind = request.POST.get("kind", "")
    form_kind = {hub.FORM_ATTENDANCE: attendance_forms.ATTENDANCE, hub.FORM_CONFIRMATION: attendance_forms.CONFIRMATION}
    if kind not in form_kind:
        raise Http404("Unknown form.")
    try:
        document = attendance_forms.generate_form(request.user, activity, form_kind[kind])
    except attendance_forms.FormError as error:
        messages.error(request, str(error))
    except (ImageUnreadable, NotAPdf, OSError):
        messages.error(request, "The PDF could not be made. Run “python manage.py check_pdf” and try again.")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, f"“{document.title}” was generated and saved to the documents of {activity.title}.")
    return redirect(hub.kind_url(kind, None))


@staff_login_required
@require_GET
def report_row(request: HttpRequest, report_id: int) -> HttpResponse:
    """One history row (HTMX polling). ?polls=<n>: how often the row has asked already. A ready or failed row comes
    back without the polling trigger, so polling stops; its new state is announced (HX-Trigger, the live region)."""
    report = get_object_or_404(GeneratedReport.objects.select_related("generated_by", "activity_type"), pk=report_id)
    polls = request.GET.get("polls", "0")
    row = pages.history_row(report, services.report_state(report), int(polls) if polls.isdigit() else 0)
    response = render(request, "reports/_row.html", {"row": row, "poll_seconds": pages.POLL_SECONDS})
    if announcement := pages.state_announcement(row):
        response["HX-Trigger"] = json.dumps({"report-state": announcement})
    return response


# --- One activity's evaluations (ASSUMPTIONS 401-402) ---------------------------------------------------------------


def activity_file_response(file: services.ActivityFile) -> HttpResponse:
    """A file made on request: always an attachment, never cached (it can hold evaluation answers)."""
    response = HttpResponse(file.data, content_type=file.content_type)
    response["Content-Disposition"] = f'attachment; filename="{file.name}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@staff_login_required
@require_GET
def evaluation_summary(request: HttpRequest, activity_id: int, file_format: str) -> HttpResponse:
    """The Evaluation tab's "Evaluation summary (PDF / Excel)": any active staff member (no names in it)."""
    activity = get_object_or_404(Activity.objects.select_related("activity_type"), pk=activity_id)
    if file_format not in GeneratedReport.Format.values:
        raise Http404("Unknown format.")
    return activity_file_response(services.evaluation_summary_file(request.user, activity, file_format))
