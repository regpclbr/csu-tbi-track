"""The report generator and paper sheet pages (ASSUMPTIONS 442-443). Thin views: the rules are in reports.generator and
reports.paper_sheets; reports.generator_pages prepares what the templates show. Every action POSTs and redirects back;
a version or sheet being written polls its status (HTMX) and reloads the page when it is done."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.permissions import staff_login_required
from documents.models import Document

from . import generator, generator_pages, paper_sheets
from .models import PaperSheetRead, ReportAttachment, ReportDraft

PAGE_SUBTITLE = ("Write activity and accomplishment reports, evaluation summaries and draft letters with the AI: numbers "
                 "from the system, every version kept")


def _need_generator(request: HttpRequest) -> None:
    if not generator.can_use(request.user):
        raise PermissionDenied(generator.NO_PERMISSION)


def _flat(error: ValidationError) -> dict:
    return {key: " ".join(values) for key, values in error.message_dict.items()} if hasattr(error, "message_dict") \
        else {"general": " ".join(error.messages)}


def _files(request: HttpRequest) -> list[generator.NewFile]:
    return ([generator.NewFile(f, ReportAttachment.Kind.DOCUMENT) for f in request.FILES.getlist("documents")]
            + [generator.NewFile(f, ReportAttachment.Kind.PHOTO) for f in request.FILES.getlist("photos")])


@staff_login_required
@require_http_methods(["GET", "POST"])
def generator_list(request: HttpRequest) -> HttpResponse:
    """GET: the "New draft" form and my drafts. POST: create a draft (generator.create_draft checks everything)."""
    _need_generator(request)
    form = generator_pages.new_form(request.POST if request.method == "POST" else request.GET, member=request.user)
    status = 200
    if request.method == "POST":
        data = request.POST
        kind = data.get("kind", "")
        activity_id, start, end, proposal = None, None, None, None
        try:
            if kind == ReportDraft.Kind.ACCOMPLISHMENT_REPORT:  # a quarter, a half or a year (the presets)
                start, end = generator.accomplishment_dates(data.get("preset"), data.get("year"))
            elif kind in generator.PROPOSAL_KINDS:  # a Draft / Scheduled activity, or typed details
                if data.get("proposal_scope") == "typed":
                    proposal = generator_pages.proposal_from_post(data)
                else:
                    activity_id = data.get("proposal_activity") or None
                    if activity_id is None:
                        raise ValidationError({"proposal_activity": [generator.PROPOSAL_ACTIVITY]})
            elif data.get("scope", "activity") != "period" or kind == ReportDraft.Kind.ACTIVITY_REPORT:
                activity_id = data.get("activity")  # the Activity Report is always about one activity
            else:
                start, end = data.get("start"), data.get("end")
            options_keys = (*generator.COMMON_OPTIONS, *generator.KIND_OPTIONS.get(kind, ()))
            draft = generator.create_draft(
                request.user, kind=kind, activity_id=activity_id, start=start, end=end, proposal=proposal,
                title=data.get("title", ""), message=data.get("message", ""), files=_files(request),
                options={key: data.get(key, "") for key in options_keys})
        except ValidationError as error:
            form = generator_pages.new_form(data, _flat(error), member=request.user)
            status = 400
        else:
            messages.success(request, "The draft is being written. It appears here in a moment.")
            return redirect("reports:generator_draft", draft_id=draft.pk)
    context = {"page_title": "Report generator", "page_subtitle": PAGE_SUBTITLE, "form": form,
               "drafts": generator_pages.draft_rows(request.user)}
    return render(request, "reports/generator_list.html", context, status=status)


@staff_login_required
@require_GET
def draft_page(request: HttpRequest, draft_id: int) -> HttpResponse:
    _need_generator(request)
    draft = generator.get_draft(request.user, draft_id)
    context = {"page_title": draft.title, "page_subtitle": f"{draft.get_kind_display()} · {generator_pages.scope_text(draft)}",
               **generator_pages.draft_page(request.user, draft, request.GET.get("version"))}
    return render(request, "reports/generator_draft.html", context)


def _back(draft: ReportDraft, version_number: int | None = None) -> HttpResponse:
    url = reverse("reports:generator_draft", args=[draft.pk])
    return redirect(f"{url}?version={version_number}" if version_number else url)


@staff_login_required
@require_POST
def ask(request: HttpRequest, draft_id: int) -> HttpResponse:
    """A follow-up request: the next version is written in the background."""
    _need_generator(request)
    draft = generator.get_draft(request.user, draft_id)
    try:
        generator.request_revision(request.user, draft, request.POST.get("message", ""))
    except (ValidationError, generator.GeneratorError) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
    return _back(draft)


@staff_login_required
@require_POST
def try_again(request: HttpRequest, draft_id: int) -> HttpResponse:
    _need_generator(request)
    draft = generator.get_draft(request.user, draft_id)
    try:
        generator.try_again(request.user, draft)
    except generator.GeneratorError as error:
        messages.error(request, str(error))
    return _back(draft)


@staff_login_required
@require_POST
def add_files(request: HttpRequest, draft_id: int) -> HttpResponse:
    _need_generator(request)
    draft = generator.get_draft(request.user, draft_id)
    try:
        added = generator.add_files(request.user, draft, _files(request))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        if added:
            messages.success(request, f"{len(added)} file{'s' if len(added) != 1 else ''} added. "
                                      "They are used from the next version on.")
    return _back(draft)


def _own_attachment(request: HttpRequest, attachment_id: int) -> ReportAttachment:
    attachment = get_object_or_404(ReportAttachment.objects.select_related("draft__activity"), pk=attachment_id)
    if attachment.draft.created_by_id != request.user.pk:
        raise Http404("No such file.")
    return attachment


@staff_login_required
@require_POST
def remove_file(request: HttpRequest, attachment_id: int) -> HttpResponse:
    _need_generator(request)
    attachment = _own_attachment(request, attachment_id)
    draft = attachment.draft
    generator.remove_file(request.user, attachment)
    messages.success(request, "The file was removed from the draft.")
    return _back(draft)


@staff_login_required
@require_GET
def download_file(request: HttpRequest, attachment_id: int) -> HttpResponse:
    """The member's own draft file: always an attachment, never /media/."""
    _need_generator(request)
    attachment = _own_attachment(request, attachment_id)
    try:
        with attachment.file_path.open("rb") as handle:
            data = handle.read()
    except (FileNotFoundError, ValueError, OSError) as error:
        raise Http404("The file is missing.") from error
    response = HttpResponse(data, content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{attachment.file_name.replace(chr(34), "")}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@staff_login_required
@require_GET
def version_status(request: HttpRequest, version_id: int) -> HttpResponse:
    """HTMX polling while a version is written: the progress line; when it is done, the page reloads (HX-Refresh)."""
    _need_generator(request)
    version = generator.get_version(request.user, version_id)
    response = render(request, "reports/_generator_progress.html",
                      {"shown": {"version": version, "status_url": request.path,
                                 "poll_seconds": generator_pages.POLL_SECONDS,
                                 "working": version.state in ("pending", "running")}})
    if version.state not in ("pending", "running"):
        response["HX-Refresh"] = "true"
    return response


@staff_login_required
@require_POST
def edit_section(request: HttpRequest, version_id: int) -> HttpResponse:
    _need_generator(request)
    version = generator.get_version(request.user, version_id)
    try:
        edited = generator.edit_section(request.user, version, request.POST.get("section", ""), request.POST.get("text", ""))
    except (ValidationError, generator.GeneratorError) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
        return _back(version.draft, version.number)
    messages.success(request, f"Saved as version {edited.number}.")
    return _back(edited.draft, edited.number)


@staff_login_required
@require_GET
def export(request: HttpRequest, version_id: int, file_format: str) -> HttpResponse:
    """DOCX or PDF of a ready version (refused while numbers are not from the system). ?inline=1 opens the PDF."""
    _need_generator(request)
    version = generator.get_version(request.user, version_id)
    if file_format not in ("pdf", "docx"):
        raise Http404("Unknown format.")
    try:
        data, name = (generator.export_pdf if file_format == "pdf" else generator.export_docx)(request.user, version)
    except generator.GeneratorError as error:
        messages.error(request, str(error))
        return _back(version.draft, version.number)
    content_type = ("application/pdf" if file_format == "pdf"
                    else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    response = HttpResponse(data, content_type=content_type)
    inline = file_format == "pdf" and request.GET.get("inline") == "1"
    response["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{name}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@staff_login_required
@require_POST
def save_to_documents(request: HttpRequest, version_id: int) -> HttpResponse:
    _need_generator(request)
    version = generator.get_version(request.user, version_id)
    try:
        document = generator.save_to_documents(request.user, version)
    except (ValidationError, generator.GeneratorError) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
    else:
        messages.success(request, f"Saved to the activity's documents as “{document.document_type.name}”.")
    return _back(version.draft, version.number)


@staff_login_required
@require_POST
def submit_to_director(request: HttpRequest, version_id: int) -> HttpResponse:
    _need_generator(request)
    version = generator.get_version(request.user, version_id)
    try:
        generator.submit_to_director(request.user, version, request.POST.get("note", ""))
    except (ValidationError, generator.GeneratorError) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
    else:
        messages.success(request, "Submitted to the Director. You can follow it in Approvals → My submissions.")
    return _back(version.draft, version.number)


# --- Paper sheets (ASSUMPTIONS 443) ------------------------------------------------------------------------------------


@staff_login_required
@require_POST
def read_sheet_from_attachment(request: HttpRequest, attachment_id: int) -> HttpResponse:
    attachment = _own_attachment(request, attachment_id)
    try:
        sheet = paper_sheets.start_from_attachment(request.user, attachment, request.POST.get("sheet_kind", ""),
                                                   request.POST.get("day") or None)
    except (ValidationError, paper_sheets.PaperSheetError) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
        return _back(attachment.draft)
    return redirect("reports:paper_sheet", read_id=sheet.pk)


@staff_login_required
@require_POST
def read_sheet_from_document(request: HttpRequest, document_id: int) -> HttpResponse:
    document = get_object_or_404(Document.objects.select_related("activity", "document_type"), pk=document_id)
    try:
        sheet = paper_sheets.start_from_document(request.user, document, request.POST.get("sheet_kind", ""),
                                                 request.POST.get("day") or None)
    except (ValidationError, paper_sheets.PaperSheetError) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
        return redirect("documents:detail", document_id=document.pk)
    return redirect("reports:paper_sheet", read_id=sheet.pk)


def _row_changes(post) -> list[dict]:
    return [{"row_id": row_id, "name": post.get(f"name_{row_id}", ""), "designation": post.get(f"designation_{row_id}", ""),
             "office": post.get(f"office_{row_id}", ""), "category_id": post.get(f"category_{row_id}", ""),
             "excluded": post.get(f"excluded_{row_id}") == "on"} for row_id in post.getlist("row")]


@staff_login_required
@require_http_methods(["GET", "POST"])
def paper_sheet(request: HttpRequest, read_id: int) -> HttpResponse:
    """GET: the rows to check, the counts and percentages. POST action: save, confirm (saves first), add_row, reopen,
    again (read the file again after a failure)."""
    sheet = paper_sheets.get_sheet(request.user, read_id)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        try:
            if action in ("save", "confirm") and sheet.state != PaperSheetRead.State.CONFIRMED:
                paper_sheets.save_rows(request.user, sheet, _row_changes(request.POST))
            if action == "confirm":
                paper_sheets.confirm(request.user, sheet)
                messages.success(request, "Confirmed: the rows are used in the generated reports from now on.")
            elif action == "add_row":
                paper_sheets.add_row(request.user, sheet)
            elif action == "reopen":
                paper_sheets.reopen(request.user, sheet)
                messages.success(request, "Reopened: correct the rows, then confirm again.")
            elif action == "again":
                paper_sheets.read_again(request.user, sheet)
            elif action == "save":
                messages.success(request, "Saved.")
        except (ValidationError, paper_sheets.PaperSheetError) as error:
            messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
        return redirect("reports:paper_sheet", read_id=sheet.pk)
    context = {"page_title": "Paper sheet", "page_subtitle": "Check the rows the AI read before the numbers are used",
               **generator_pages.sheet_page(sheet)}
    return render(request, "reports/paper_sheet.html", context)


@staff_login_required
@require_GET
def paper_sheet_status(request: HttpRequest, read_id: int) -> HttpResponse:
    sheet = paper_sheets.get_sheet(request.user, read_id)
    response = render(request, "reports/_sheet_progress.html", {"sheet": sheet, "poll_seconds": generator_pages.POLL_SECONDS})
    if sheet.state != PaperSheetRead.State.READING:
        response["HX-Refresh"] = "true"
    return response
