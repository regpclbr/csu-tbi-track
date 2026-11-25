"""The Approvals pages (ASSUMPTIONS 437): "For your approval" (the inbox), all submissions (reviewers), my submissions,
"Submit to the Director", one submission's page with its actions, and its files. Thin views: every rule is in
approvals.services / approvals.inbox (which check the permissions again). Refusals come back as toasts."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import is_htmx, staff_login_required
from activities.models import Activity
from activities.services import _paginate
from documents.models import DocumentType
from documents.pages import upload_limits
from documents.services import RETURN_REASON_MAX_LENGTH

from . import inbox, pages, services, signing
from .models import Submission

LIST_SUBTITLE = "Work waiting for your approval: certificates, submissions and documents to review"


def _flat(error: ValidationError) -> str:
    if hasattr(error, "error_dict"):
        return " ".join(message for messages_ in error.message_dict.values() for message in messages_)
    return " ".join(error.messages)


def _tabs_context(request: HttpRequest, tab: str) -> dict:
    from .assignments import assigned_to_me

    return {"tab": tab, "has_inbox": inbox.has_inbox(request.user), "can_review": services.can_review(request.user),
            "inbox_total": inbox.inbox_counts(request.user)["total"] if inbox.has_inbox(request.user) else 0,
            "assigned_total": assigned_to_me(request.user).count()}


@staff_login_required
@require_GET
def inbox_page(request: HttpRequest) -> HttpResponse:
    """GET kind, activity, q: everything waiting for me (approvals.inbox.inbox), oldest first. A member who approves
    nothing goes to "My submissions". HTMX (the filters): only the list."""
    if not inbox.has_inbox(request.user):
        return redirect("approvals:mine")
    filters = pages.parse_inbox_filters(request.GET)
    items = inbox.inbox(request.user, filters.as_dict())
    context = {
        **_tabs_context(request, "inbox"), "page_title": "For your approval", "page_subtitle": LIST_SUBTITLE,
        "filters": filters, "items": items, "kinds": [(kind, inbox.KIND_LABELS[kind]) for kind in inbox.kinds_for(request.user)],
        "kind_tones": pages.KIND_TONES, "kind_icons": pages.KIND_ICONS,
        "activities": pages.activity_choices(), "list_url": reverse("approvals:inbox"),
    }
    if is_htmx(request):
        return render(request, "approvals/_inbox.html", context)
    return render(request, "approvals/list.html", context)


def _submission_list(request: HttpRequest, *, mine: bool) -> HttpResponse:
    status = request.GET.get("status", "")
    q = request.GET.get("q", "")
    result = _paginate(services.list_submissions(request.user, mine=mine, status=status, q=q), request.GET.get("page", 1), 20)
    context = {
        **_tabs_context(request, "mine" if mine else "all"),
        "page_title": "My submissions" if mine else "All submissions",
        "page_subtitle": ("Letters, reports and other work you submitted to the Director" if mine
                          else "Every submission to the Director, newest first"),
        "result": result, "rows": [{"submission": s, "state": pages.status_badge(s)} for s in result.items],
        "status": status if status in Submission.Status.values else "", "q": q.strip()[:100],
        "statuses": Submission.Status.choices,
        "query": "&".join(f"{k}={v}" for k, v in (("status", status), ("q", q.strip()[:100])) if v),
        "list_url": reverse("approvals:mine" if mine else "approvals:all"),
    }
    if is_htmx(request):
        return render(request, "approvals/_submissions.html", context)
    return render(request, "approvals/list.html", context)


@staff_login_required
@require_GET
def mine(request: HttpRequest) -> HttpResponse:
    return _submission_list(request, mine=True)


@staff_login_required
@require_GET
def all_submissions(request: HttpRequest) -> HttpResponse:
    if not services.can_review(request.user):
        raise PermissionDenied("You do not have permission to approve or return documents.")
    return _submission_list(request, mine=False)


# --- Submit -----------------------------------------------------------------------------------------------------------


def _form_context(values: dict, errors: dict) -> dict:
    return {"page_title": "Submit to the Director",
            "page_subtitle": "Send a letter, report or other document to the Director for approval",
            "values": values, "errors": errors, "activities": pages.activity_choices(), "types": pages.type_choices(),
            "limits": upload_limits(), "note_max": services.NOTE_MAX_LENGTH}


@staff_login_required
def submit_page(request: HttpRequest) -> HttpResponse:
    """GET the form (?activity=<id> preselects it); POST title, file, note, activity, document_type."""
    if request.method != "POST":
        raw = request.GET.get("activity", "")
        return render(request, "approvals/submit.html", _form_context({"activity": int(raw) if raw.isdigit() else None}, {}))
    raw_activity, raw_type = request.POST.get("activity", ""), request.POST.get("document_type", "")
    values = {"title": request.POST.get("title", ""), "note": request.POST.get("note", ""),
              "activity": int(raw_activity) if raw_activity.isdigit() else None,
              "document_type": int(raw_type) if raw_type.isdigit() else None}
    activity = Activity.objects.filter(pk=values["activity"]).first() if values["activity"] else None
    document_type = DocumentType.objects.filter(pk=values["document_type"]).first() if values["document_type"] else None
    try:
        submission = services.submit(request.user, request.FILES.get("file"), values["title"], values["note"],
                                     activity, document_type)
    except ValidationError as error:
        errors = {key: " ".join(value) for key, value in error.message_dict.items()}
        return render(request, "approvals/submit.html", _form_context(values, errors), status=400)
    except services.SubmissionError as error:
        return render(request, "approvals/submit.html", _form_context(values, {"form": str(error)}), status=400)
    messages.success(request, f'"{submission.title}" was submitted. You get a notification when it is approved or returned.')
    return redirect("approvals:detail", submission_id=submission.pk)


# --- One submission ---------------------------------------------------------------------------------------------------


def _submission(request: HttpRequest, submission_id: int) -> Submission:
    return get_object_or_404(services.visible_submissions(request.user), pk=submission_id)


@staff_login_required
@require_GET
def detail(request: HttpRequest, submission_id: int) -> HttpResponse:
    submission = _submission(request, submission_id)
    actions = services.allowed_actions(request.user, submission)
    sign = {}
    if actions["approve"]:
        refused = services.sign_refused_reason(request.user, submission)  # also "never delegated" for an assignee
        sign = {"refused": refused, "noted_page": None, "pages": 1}
        if not refused:
            try:
                data = services._original_bytes(submission)
                sign["pages"] = signing.page_count(data)
                sign["noted_page"] = signing.noted_page(data)
            except (signing.SigningError, OSError) as error:
                sign["refused"] = str(error) if isinstance(error, signing.SigningError) else "The file is missing."
    context = {
        "assign": pages.assign_panel(request.user, submission=submission)
        if submission.status == Submission.Status.SUBMITTED else None,  # ASSUMPTIONS 437
        "my_assignments": pages.my_assignments_on(request.user, submission=submission),
        "page_title": submission.title, "page_subtitle": "A submission to the Director",
        "submission": submission, "state": pages.status_badge(submission), "actions": actions, "sign": sign,
        "events": list(submission.events.select_related("member", "on_behalf_of")),
        "positions": pages.position_choices(), "types": pages.type_choices(), "stamp_note": pages.STAMP_NOTE,
        "reason_max": RETURN_REASON_MAX_LENGTH, "note_max": services.NOTE_MAX_LENGTH, "limits": upload_limits(),
    }
    return render(request, "approvals/detail.html", context)


def _back(submission_id: int) -> HttpResponse:
    return redirect("approvals:detail", submission_id=submission_id)


def _run(request: HttpRequest, submission_id: int, action, success: str) -> HttpResponse:
    submission = _submission(request, submission_id)
    try:
        action(submission)
    except ValidationError as error:
        messages.error(request, _flat(error))
    except (services.SubmissionError, PermissionDenied) as error:
        messages.error(request, str(error) or "You do not have permission to do this.")
    else:
        messages.success(request, success.format(title=submission.title))
    return _back(submission.pk)


@staff_login_required
@require_POST
def approve(request: HttpRequest, submission_id: int) -> HttpResponse:
    """POST sign (true/absent), placement, page."""
    sign = request.POST.get("sign") == "true"
    placement = services.parse_placement(request.POST)
    return _run(request, submission_id,
                lambda s: services.approve_submission(request.user, s, sign=sign, placement=placement),
                '"{title}" was approved' + (" and signed." if sign else "."))


@staff_login_required
@require_POST
def return_submission(request: HttpRequest, submission_id: int) -> HttpResponse:
    return _run(request, submission_id,
                lambda s: services.return_submission(request.user, s, request.POST.get("reason", "")),
                '"{title}" was returned to the member who submitted it.')


@staff_login_required
@require_POST
def unapprove(request: HttpRequest, submission_id: int) -> HttpResponse:
    return _run(request, submission_id,
                lambda s: services.unapprove_submission(request.user, s, request.POST.get("reason", "")),
                'The approval of "{title}" was taken back. It is now Returned.')


@staff_login_required
@require_POST
def resubmit(request: HttpRequest, submission_id: int) -> HttpResponse:
    return _run(request, submission_id,
                lambda s: services.resubmit(request.user, s, request.FILES.get("file"), request.POST.get("note", "")),
                '"{title}" was submitted again.')


@staff_login_required
@require_POST
def save_to_documents(request: HttpRequest, submission_id: int) -> HttpResponse:
    raw = request.POST.get("document_type", "")
    document_type = DocumentType.objects.filter(pk=raw).first() if raw.isdigit() else None
    return _run(request, submission_id,
                lambda s: services.save_to_documents(request.user, s, document_type),
                '"{title}" was saved to the activity\'s documents.')


# --- Files ------------------------------------------------------------------------------------------------------------


def _file_response(file: services.SubmissionFile, inline: bool) -> FileResponse:
    response = FileResponse(file.handle, content_type=file.content_type)
    response["Content-Disposition"] = content_disposition_header(not inline, file.name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@staff_login_required
@require_GET
def download(request: HttpRequest, submission_id: int, which: str) -> FileResponse:
    """GET the original (`which` = "original") or the signed copy ("signed"); ?inline=1 opens a PDF in the browser
    (a new tab), anything else is an attachment. Only for members who may open the submission (404 otherwise)."""
    submission = _submission(request, submission_id)
    if which not in ("original", "signed"):
        raise Http404()
    try:
        file = services.open_file(submission, signed=which == "signed")
    except FileNotFoundError as error:
        raise Http404("The file is missing.") from error
    return _file_response(file, inline=request.GET.get("inline") == "1" and file.content_type == "application/pdf")


@staff_login_required
@require_GET
def sign_preview(request: HttpRequest, submission_id: int) -> HttpResponse:
    """GET placement, page: the stamped copy as it would be saved (never stored), inline in a new tab."""
    submission = _submission(request, submission_id)
    try:
        data = services.signed_preview(request.user, submission, services.parse_placement(request.GET))
    except services.SubmissionError as error:
        messages.error(request, f"No preview: {error}")
        return _back(submission.pk)
    response = HttpResponse(data, content_type="application/pdf")
    stem = submission.file_name.rsplit(".", 1)[0] or "submission"
    response["Content-Disposition"] = content_disposition_header(False, f"{stem}-signed-preview.pdf")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


# --- Assignments (ASSUMPTIONS 437) -----------------------------------------------------------------------------------


def _next(request: HttpRequest, default: str) -> str:
    from config.links import is_internal_path

    target = request.POST.get("next", "")
    return target if target and is_internal_path(request, target) else default


@staff_login_required
@require_POST
def assign(request: HttpRequest) -> HttpResponse:
    """POST kind, assigned_to, activity | document | submission | report_title, note, due_date (YYYY-MM-DD), next:
    approvals.assignments.assign_work (can_assign_work + the flag of the work; every rule checked there)."""
    from datetime import date as date_type

    from documents.models import Document

    from . import assignments
    from .models import WorkAssignment

    def pick(model, key):
        raw = request.POST.get(key, "")
        return model.objects.filter(pk=raw).first() if raw.isdigit() else None

    back = _next(request, reverse("approvals:assigned"))
    raw_due = request.POST.get("due_date", "").strip()
    try:
        due = date_type.fromisoformat(raw_due) if raw_due else None
    except ValueError:
        messages.error(request, "Not assigned: give the due date as YYYY-MM-DD.")
        return redirect(back)
    from accounts.models import TBIMember

    try:
        assignment = assignments.assign_work(
            request.user, request.POST.get("kind", ""), pick(TBIMember, "assigned_to"),
            activity=pick(Activity, "activity"), document=pick(Document, "document"),
            submission=pick(Submission, "submission"), report_title=request.POST.get("report_title", ""),
            note=request.POST.get("note", ""), due_date=due)
    except ValidationError as error:
        messages.error(request, "Not assigned: " + _flat(error))
    except (assignments.AssignmentError, PermissionDenied) as error:
        messages.error(request, f"Not assigned: {error}")
    else:
        messages.success(request, f"{WorkAssignment.Kind(assignment.kind).label}: assigned to "
                                  f"{assignment.assigned_to.name}. They got a notification.")
    return redirect(back)


def _assignment_action(request: HttpRequest, assignment_id: int, action, success: str) -> HttpResponse:
    from . import assignments
    from .models import WorkAssignment

    assignment = get_object_or_404(WorkAssignment, pk=assignment_id)
    try:
        action(assignment)
    except (assignments.AssignmentError, PermissionDenied) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, success)
    return redirect(_next(request, reverse("approvals:assigned")))


@staff_login_required
@require_POST
def assignment_done(request: HttpRequest, assignment_id: int) -> HttpResponse:
    """POST (optional generated_report = one of the assignee's reports): approvals.assignments.mark_done."""
    from reports.models import GeneratedReport

    from . import assignments

    raw = request.POST.get("generated_report", "")
    report = GeneratedReport.objects.filter(pk=raw).first() if raw.isdigit() else None
    return _assignment_action(request, assignment_id, lambda a: assignments.mark_done(request.user, a, report),
                              "Marked done. The member who assigned it was told.")


@staff_login_required
@require_POST
def assignment_remove(request: HttpRequest, assignment_id: int) -> HttpResponse:
    from . import assignments

    return _assignment_action(request, assignment_id, lambda a: assignments.remove_assignment(request.user, a),
                              "The assignment was removed. The member was told.")


@staff_login_required
@require_GET
def assigned(request: HttpRequest) -> HttpResponse:
    """GET show=open|all: "Assigned to me" (approvals.assignments.assigned_to_me), due first."""
    from reports.models import GeneratedReport

    from . import assignments

    show_all = request.GET.get("show") == "all"
    rows = pages.assignment_rows(assignments.assigned_to_me(request.user, include_closed=show_all)[:200])
    context = {**_tabs_context(request, "assigned"), "page_title": "Assigned to me",
               "page_subtitle": "Work the Director assigned to you, with its due date",
               "rows": rows, "show_all": show_all,
               "my_reports": list(GeneratedReport.objects.filter(generated_by=request.user).order_by("-generated_at")[:20])}
    return render(request, "approvals/list.html", context)
