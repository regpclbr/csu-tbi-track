"""The two generated paper forms (ASSUMPTIONS 434, NAVIGATU UAT): "Attendance" (one sheet per activity day) and
"Confirmation of Attendance" (one sheet for the whole activity). They replace the paper forms the participants signed.

Printable A4 PDFs through certificates.pdf.get_renderer() (WeasyPrint; a fake in tests), template
templates/registrations/attendance_forms_pdf.html; signature images go in as data: URIs only. Each one is saved as an
activity DOCUMENT through documents.services.upload_document (an approved copy, item 432), so it counts in the checklist
and the payment packet: "Attendance" as the Attendance Sheet type, the other as the Confirmation of Attendance type.
Generating again adds a new document (the older one can be deleted). Staff can still upload a scanned paper sheet.

"Prepared by": the member who generates it, with their POSITION (item 429). Header: a plain placeholder until NAVIGATU
gives the letterhead files (open question, CLAUDE.md).
"""

from dataclasses import dataclass
from datetime import date, datetime

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission_for
from activities.models import Activity, ActivityStatus

from . import signatures
from .attendance import checked_in_text
from .models import AttendanceDay, ParticipantSignature, Registration

ATTENDANCE = "attendance"
CONFIRMATION = "confirmation"
KINDS = (ATTENDANCE, CONFIRMATION)
GENERATE_PERMISSION = Permission.CREATE_ACTIVITY
DOCUMENT_TYPE_NAMES = {ATTENDANCE: "Attendance Sheet", CONFIRMATION: settings.CONFIRMATION_OF_ATTENDANCE}
TITLES = {ATTENDANCE: "Attendance (generated)", CONFIRMATION: "Confirmation of Attendance (generated)"}
FORM_CODE = "F-PROC-COA-005 Rev.03, 10-10-2023"
FOOTER_NOTE = ("NOTE: Participants who signified to attend the meeting/activity and fail to be present on the day of the "
               "meeting/activity will shoulder the expenses of the procured meals allotted for them.")
PLACEHOLDER_HEADER = ("Caraga State University", "CSU Technology Business Incubator")

NO_ATTENDANCE = "Nobody has attended yet: the Attendance form lists the people checked in on each day."
NO_PRE_REGISTRATION = "Nobody pre-registered: the Confirmation of Attendance lists the online registrations."
DRAFT_REFUSED = "A Draft activity has no attendance forms."
SIGNATURES_DELETED = ("The participants' signatures were deleted after the forms were kept (retention). Upload a "
                      "scanned sheet instead.")
TYPE_MISSING = "The document type “{name}” does not exist or is not active. Ask the System Administrator."


class FormError(Exception):
    """Generating was refused; the message is safe to show."""


def long_date(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def date_span(start: date, end: date) -> str:
    """"October 21, 2026", "October 21–22, 2026", "October 31 – November 1, 2026", "December 31, 2026 – January 1, 2027"."""
    if start == end:
        return long_date(start)
    if start.year != end.year:
        return f"{long_date(start)} – {long_date(end)}"
    if start.month != end.month:
        return f"{start:%B} {start.day} – {end:%B} {end.day}, {end.year}"
    return f"{start:%B} {start.day}–{end.day}, {end.year}"


@dataclass
class SignatureCell:
    """What the Signature column prints: an image, a typed name, or a short note."""

    image: str | None = None
    typed_name: str | None = None
    note: str = ""


def _signature_cell(registration: Registration, *, staff_note: str = "") -> SignatureCell:
    try:
        signature = registration.signature
    except ParticipantSignature.DoesNotExist:
        return SignatureCell(note=staff_note)
    if signature.is_deleted:
        return SignatureCell(note=signatures.DELETED_TEXT)
    if signature.kind == ParticipantSignature.Kind.TYPED:
        return SignatureCell(typed_name=signature.typed_name)
    image = signatures.data_uri(signature)
    return SignatureCell(image=image) if image else SignatureCell(note="Signature file missing")


METHOD_NOTES = {
    AttendanceDay.Method.STAFF: "Marked by staff",
    AttendanceDay.Method.WET_SIGNATURE: "Signed on paper",
    AttendanceDay.Method.EMAIL: "Confirmed by email",
}


def attendance_pages(activity: Activity) -> list[dict]:
    """One page per activity day with attendance: the people of that day by name, with their designation, office,
    signature (image / typed name; "Marked by staff" when staff marked them and they have none) and check-in time."""
    rows = (AttendanceDay.objects.filter(registration__activity=activity)
            .select_related("registration__signature").order_by("day", "registration__name", "registration_id"))
    pages: dict[date, list[dict]] = {}
    for row in rows:
        registration = row.registration
        pages.setdefault(row.day, []).append({
            "name": registration.name, "designation": registration.designation,
            "office": registration.office_agency,
            "signature": _signature_cell(registration, staff_note=METHOD_NOTES.get(row.method, "")),
            "time": checked_in_text(row),
            "walk_in": registration.registration_source == Registration.Source.WALK_IN,
        })
    return [{"date": long_date(day), "rows": _numbered(people)} for day, people in sorted(pages.items())]


def confirmation_rows(activity: Activity) -> list[dict]:
    """Everyone who pre-registered (online; walk-ins never signified), by name: the signature goes under
    "Will attend" or "Will not attend" (cancelled)."""
    registrations = (activity.registrations.exclude(registration_source=Registration.Source.WALK_IN)
                     .select_related("signature").order_by("name", "registration_id"))
    return _numbered([{
        "name": registration.name, "designation": registration.designation,
        "will_attend": registration.cancelled_at is None, "signature": _signature_cell(registration),
    } for registration in registrations])


def _numbered(rows: list[dict]) -> list[dict]:
    return [{"number": number, **row} for number, row in enumerate(rows, start=1)]


def prepared_by(member: TBIMember) -> dict:
    """Name and POSITION (item 429; empty when the member has none)."""
    return {"name": member.name, "position": member.position.name if member.position_id else ""}


def form_html(activity: Activity, kind: str, member: TBIMember, now: datetime | None = None) -> str:
    context = {
        "kind": kind, "activity": activity, "header": PLACEHOLDER_HEADER, "form_code": FORM_CODE,
        "footer_note": FOOTER_NOTE, "prepared_by": prepared_by(member),
        "generated_at": timezone.localtime(now or timezone.now()),
        "dates": date_span(activity.start_date, activity.end_date),
    }
    if kind == ATTENDANCE:
        context["pages"] = attendance_pages(activity)
    else:
        context["rows"] = confirmation_rows(activity)
    return render_to_string("registrations/attendance_forms_pdf.html", context)


def refused_reason(activity: Activity, kind: str) -> str | None:
    """Why this form cannot be generated now, or None. The Documents tab shows it instead of the button."""
    if activity.status.name == ActivityStatus.DRAFT:
        return DRAFT_REFUSED
    if kind == ATTENDANCE:
        involved = Registration.objects.filter(activity=activity, attendance_days__isnull=False).distinct()
        if not involved.exists():
            return NO_ATTENDANCE
    else:
        involved = activity.registrations.exclude(registration_source=Registration.Source.WALK_IN)
        if not involved.exists():
            return NO_PRE_REGISTRATION
    if involved.filter(signatures.deleted_signature_q()).exists():
        return SIGNATURES_DELETED
    return None


def _document_type(kind: str):
    from documents.models import DocumentType

    name = DOCUMENT_TYPE_NAMES[kind]
    document_type = DocumentType.objects.filter(name=name, is_active=True).first()
    if document_type is None:
        raise FormError(TYPE_MISSING.format(name=name))
    return document_type


def file_name(activity: Activity, kind: str) -> str:
    return f"{'attendance' if kind == ATTENDANCE else 'confirmation-of-attendance'}-activity-{activity.pk}.pdf"


def generate_form(member: TBIMember, activity: Activity, kind: str, now: datetime | None = None):
    """Render one form and save it as an activity document (an approved copy). Needs can_create_activity. Returns the
    Document. Raises FormError (refused_reason, missing type) or certificates.pdf errors from the renderer."""
    from certificates.pdf import get_renderer
    from documents.services import upload_document

    require_permission_for(member, GENERATE_PERMISSION, activity)  # or the activity's assignment (ASSUMPTIONS 460)
    if kind not in KINDS:
        raise FormError("Unknown form.")
    if reason := refused_reason(activity, kind):
        raise FormError(reason)
    document_type = _document_type(kind)
    pdf = get_renderer().render(form_html(activity, kind, member, now))
    upload = SimpleUploadedFile(file_name(activity, kind), pdf, content_type="application/pdf")
    return upload_document(member, activity, document_type, upload, title=TITLES[kind], now=now)
