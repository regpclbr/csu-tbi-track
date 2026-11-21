"""Business logic for certificates: design (template and signatories), automatic generation, approval,
the final PDF, issuing and emailing. Views, the Django admin and tasks call these functions.

The life of a certificate (status rules in set_certificate_status; ASSUMPTIONS 357, 361, 362):

    created by the System --> Generated ("Waiting for approval") --approved when ready--> Approved --> Issued
                                       <---- new signature / "Apply the current design" (approval cleared) ----

- The System creates the certificates when the activity becomes Conducted (generate_missing_certificates: one per
  attendee, one per resource person; also attendees marked later; the 5-minute job catches up). No Generate button.
- Approval is per ACTIVITY (ASSUMPTIONS 369): a member with can_approve_certificates checks the preview and approves
  the activity once (approve_activity_certificates: ACTIVITY.certificates_approved_by / _at). From then on every
  certificate of the activity that is ready (readiness(): both signature images; a participant's evaluation) is
  approved by the System in the approver's name (approve_ready_certificates): now, at each later evaluation, and for
  certificates created later. A change to what is printed (signatory, title, a new version of a person, "Apply the
  current design", the certificate text) cancels the approval (cancel_certificate_approval): not-Issued certificates
  wait again. Apply the current design, hand over, resend: can_create_activity.
- The wording (ASSUMPTIONS 370): CERTIFICATE_TEXT rows (a default per type, optional custom text per activity),
  versioned; each certificate keeps the row it was printed with (certificates.wording fills the placeholders).
- Signatories (ASSUMPTIONS 363, 418): a list of people (CERTIFICATE_SIGNATORY) managed ONLY by can_manage_signatories
  (the Director by default: add, edit, signature images, deactivate, defaults); each activity chooses a left and a right
  one from it (can_create_activity or can_approve_certificates, unchanged) (ACTIVITY_SIGNATORY; a new activity starts with the defaults). A row that a
  certificate uses never changes: a new image or a name/position edit is a new version (new row) that every activity
  moves to. Changing an activity's slot or a new version refreshes the certificates that are not Issued (the PDF is
  rendered again, Approved ones wait for approval again).
- Issued certificates keep what they were printed with. One whose signatories are no longer the activity's is
  "Issued with an old signature" (computed: outdated_q); a member with can_approve_certificates may reissue it
  (reissue_certificates: same number, still Issued, new PDF, emailed again / reprinted; ASSUMPTIONS 364).

- Participant (Certificate of Participation, by email): the name printed and the address come
  from the participant's EVALUATION_SUBMISSION. Approval needs the evaluation, so right after it a background task
  renders the PDF, issues the certificate and queues the email (certificates approved earlier without an evaluation
  are issued when it arrives).
- Resource person (Certificate of Appreciation, hard copy): the PDF is rendered when it is
  Approved; staff mark it Issued when the hard copy is handed over. Never emailed.
"""

import base64
from dataclasses import dataclass
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import connection, transaction
from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet, Value
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone
from PIL import Image

from accounts.models import TBIMember
from accounts.permissions import (
    Permission,
    has_permission,
    require_any_permission,
    require_permission,
    require_staff_member,
)
from config.files import write_file_atomically
from activities.models import Activity, ActivityResourcePerson, ActivityStatus
from activities.services import PageResult, _paginate
from documents import services as document_services
from documents.models import Document, DocumentType
from registrations.models import EvaluationSubmission, Registration

from . import emails, imaging, pdf, wording
from .models import (
    ActivitySignatory, Certificate, CertificateNumberSequence, CertificateSignatory, CertificateTemplate, CertificateText,
    Slot,
)

logger = logging.getLogger("certificates")
audit_logger = logging.getLogger("certificates.audit")  # signatory changes and reissues (IDs only)

Status = Certificate.Status
RecipientType = Certificate.RecipientType
DeliveryMethod = Certificate.DeliveryMethod

DEFAULT_PAGE_SIZE = 20
TEXT_PARTICIPATION = CertificateText.CertificateType.PARTICIPATION
TEXT_APPRECIATION = CertificateText.CertificateType.APPRECIATION

# The only status moves allowed. Forward one step at a time, plus one backward move:
# Approved -> Generated when the certificate is regenerated (it must be approved again).
# Issued is final: a wrong certificate is replaced by a new one with a new number.
ALLOWED_MOVES: dict[str, set[str]] = {
    Status.GENERATED: {Status.APPROVED},
    Status.APPROVED: {Status.ISSUED, Status.GENERATED},
    Status.ISSUED: set(),
}
SLOTS = (Slot.LEFT, Slot.RIGHT)  # printed left to right
SLOT_LABELS = {Slot.LEFT: "Left", Slot.RIGHT: "Right"}
SLOT_FIELDS = {Slot.LEFT: "left_signatory", Slot.RIGHT: "right_signatory"}  # CERTIFICATE columns
SIGNATORY_HISTORY_LABELS = {Slot.LEFT: "Certificate signatory (left)", Slot.RIGHT: "Certificate signatory (right)"}

CERTIFICATE_DOCUMENT_TYPE = "Certificate"  # DOCUMENT_TYPE for evidence uploads (seeded name, item 48)
NUMBER_LOCK_KEY = 9_000_001  # PostgreSQL advisory lock: one number allocation at a time


class CertificateError(Exception):
    """An action on certificates was refused. The message is safe to show to the member.

    `problems`: for a refused bulk action, one "<certificate number>: <reason>" per refused certificate
    (the message lists them too); empty otherwise. The pages show them as a list (phase F4)."""

    def __init__(self, message: str = "", problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = list(problems or [])


class CertificateStatusError(CertificateError):
    """Raised when a certificate cannot move to the requested status."""


class SlotError(CertificateError):
    """A refused choice of an activity's signatory or title (ASSUMPTIONS 365). `field`: the form field it belongs to
    ("signatory_left", "title_right", ...), so the activity form can show it under that field."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


# --- Image checks (template background and signatures) -----------------------------------------

IMAGE_EXTENSIONS = {"png": "png", "jpg": "jpg", "jpeg": "jpg"}
PILLOW_FORMATS = {"png": "PNG", "jpg": "JPEG"}
A4_LANDSCAPE_RATIO = 297 / 210
RATIO_TOLERANCE = 0.03  # 3%
MIN_TEMPLATE_SIZE = (1754, 1240)  # A4 at 150 dpi, in pixels


def _check_image(file: UploadedFile | None, field: str, allowed: set[str]) -> tuple[str, tuple[int, int]]:
    """Same idea as Step 6: the real content is checked, not the name. Returns (extension, (width, height))."""
    kinds = " or ".join(sorted(e.upper() for e in allowed))
    if file is None:
        raise ValidationError({field: ["Choose an image to upload."]})
    extension = IMAGE_EXTENSIONS.get(PurePosixPath(file.name or "").suffix.lower().lstrip("."))
    if extension not in allowed:
        raise ValidationError({field: [f"Only {kinds} images can be uploaded."]})
    if not file.size:
        raise ValidationError({field: ["The file is empty."]})
    if file.size > settings.CERTIFICATE_IMAGE_MAX_MB * 1024 * 1024:
        raise ValidationError({field: [f"The image is larger than {settings.CERTIFICATE_IMAGE_MAX_MB} MB."]})
    file.seek(0)
    head = file.read(16)
    file.seek(0)
    signature = document_services.PNG_SIGNATURE if extension == "png" else document_services.JPEG_SIGNATURE
    if not head.startswith(signature):
        raise ValidationError({field: [f"The file's content is not a valid {extension.upper()} image."]})
    try:
        with Image.open(file) as image:
            image_format, size = image.format, image.size
            image.verify()  # reads the whole file: refuses damaged images
    except Exception as error:  # Pillow raises many kinds (including decompression bombs)
        raise ValidationError({field: [f"The file's content is not a valid {extension.upper()} image."]}) from error
    finally:
        file.seek(0)
    if image_format != PILLOW_FORMATS[extension]:
        raise ValidationError({field: [f"The file's content is not a valid {extension.upper()} image."]})
    return extension, size


def validate_template_image(file: UploadedFile | None) -> str:
    """PNG or JPG, A4 landscape (297 x 210, within 3%), at least 1754 x 1240 pixels."""
    extension, (width, height) = _check_image(file, "file", {"png", "jpg"})
    if abs(width / height - A4_LANDSCAPE_RATIO) > A4_LANDSCAPE_RATIO * RATIO_TOLERANCE:
        raise ValidationError({"file": [f"The image must be A4 landscape (297 x 210 proportions); this one is {width} x {height} pixels."]})
    if width < MIN_TEMPLATE_SIZE[0] or height < MIN_TEMPLATE_SIZE[1]:
        raise ValidationError({"file": [f"The image must be at least {MIN_TEMPLATE_SIZE[0]} x {MIN_TEMPLATE_SIZE[1]} pixels; this one is {width} x {height}."]})
    return extension


def validate_signature_image(file: UploadedFile | None) -> str:
    """PNG or JPG (ASSUMPTIONS 361), at most CERTIFICATE_IMAGE_MAX_MB, checked by its real content. A PNG with a
    transparent background prints cleanly over the design; a JPG prints with its white background."""
    return _check_image(file, "file", {"png", "jpg"})[0]


# What happened to an uploaded signature (ASSUMPTIONS 366); the upload dialog's preview says it.
BACKGROUND_REMOVED, BACKGROUND_ALREADY_TRANSPARENT, BACKGROUND_KEPT = "removed", "already_transparent", "kept"
BACKGROUND_NOTES = {
    BACKGROUND_REMOVED: "The white background was made transparent. This is how it will be stored (a PNG).",
    BACKGROUND_ALREADY_TRANSPARENT: "This image already has a transparent background: it is stored as uploaded.",
    BACKGROUND_KEPT: "The background is kept: the image is stored as uploaded.",
}


def prepare_signature_file(file: UploadedFile | None, keep_background: bool = False):
    """Check an uploaded signature (validate_signature_image: PNG or JPG, size limit, real content) and return
    (the file to store, what happened). Unless keep_background, white and near-white background pixels become
    transparent (certificates.imaging) and the result is a PNG; an image that already has transparency is stored
    unchanged."""
    validate_signature_image(file)
    if keep_background:
        return file, BACKGROUND_KEPT
    file.seek(0)
    with Image.open(file) as image:
        image.load()
        if imaging.has_transparency(image):
            file.seek(0)
            return file, BACKGROUND_ALREADY_TRANSPARENT
        cleaned = imaging.remove_white_background(image)
    file.seek(0)
    return ContentFile(imaging.png_bytes(cleaned), name="signature.png"), BACKGROUND_REMOVED


def signature_preview(member: TBIMember, file: UploadedFile | None, keep_background: bool = False) -> dict:
    """The upload dialog's preview, before saving (nothing is stored): {"data_uri": the image as it will be stored,
    "note": what happened, "outcome"}. can_manage_signatories (ASSUMPTIONS 418)."""
    require_permission(member, Permission.MANAGE_SIGNATORIES)
    stored, outcome = prepare_signature_file(file, keep_background)
    stored.seek(0)
    content = stored.read()
    mime = "image/png" if content.startswith(document_services.PNG_SIGNATURE) else "image/jpeg"
    return {"data_uri": f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}",
            "note": BACKGROUND_NOTES[outcome], "outcome": outcome}


def _clean_name(name: str, field: str = "name") -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError({field: ["This field is required."]})
    if len(name) > 255:
        raise ValidationError({field: ["At most 255 characters."]})
    return name


def _delete_file(field_file) -> None:
    if field_file and field_file.name:
        field_file.storage.delete(field_file.name)


# --- Templates (Admin, can_manage_users) ----------------------------------------------------------
# Files never change after upload: a new design is a new row, so existing certificates keep exactly what they were
# generated with. Nothing is deleted (PROTECT); rows are deactivated.


def upload_template(member: TBIMember, name: str, file: UploadedFile | None, activate: bool = False) -> CertificateTemplate:
    """Upload a new certificate design (background image). Optionally make it the active one."""
    require_permission(member, Permission.MANAGE_USERS)
    name = _clean_name(name)
    validate_template_image(file)
    template = CertificateTemplate(name=name, uploaded_by=member, is_active=False)
    template.file_path.save(file.name, file, save=False)  # stored under a random name (models.template_upload_to)
    try:
        template.save()
    except Exception:
        _delete_file(template.file_path)
        raise
    if activate:
        template = activate_template(member, template)
    return template


@transaction.atomic
def activate_template(member: TBIMember, template: CertificateTemplate) -> CertificateTemplate:
    """Make this the only active design (the previous one is deactivated in the same transaction)."""
    require_permission(member, Permission.MANAGE_USERS)
    list(CertificateTemplate.objects.select_for_update().filter(Q(is_active=True) | Q(pk=template.pk)))
    CertificateTemplate.objects.filter(is_active=True).exclude(pk=template.pk).update(is_active=False)
    CertificateTemplate.objects.filter(pk=template.pk).update(is_active=True)
    template.refresh_from_db()
    return template


def deactivate_template(member: TBIMember, template: CertificateTemplate) -> CertificateTemplate:
    require_permission(member, Permission.MANAGE_USERS)
    CertificateTemplate.objects.filter(pk=template.pk).update(is_active=False)
    template.refresh_from_db()
    return template


# --- The signatory list (can_manage_signatories; ASSUMPTIONS 363, 418) ---------------------------
# A row that a certificate uses never changes (Issued certificates keep exactly what they were printed with): a new
# image or a name/position edit creates a new version of the person (a new row) and every activity moves to it.
# Nobody is deleted (PROTECT); people are deactivated.


def signatory_label(signatory: CertificateSignatory) -> str:
    """ "Dr. Maria Santos (University President)": how a person is named in messages and History."""
    return f"{signatory.name} ({signatory.position})"


def _clean_person(name: str, position: str) -> tuple[str, str]:
    errors: dict[str, list[str]] = {}
    values = {}
    for field, value in (("name", name), ("position", position)):
        try:
            values[field] = _clean_name(value, field)
        except ValidationError as error:
            errors.update(error.message_dict)
    if errors:
        raise ValidationError(errors)
    return values["name"], values["position"]


def active_signatories() -> QuerySet:
    """The people offered in the dropdowns (name order)."""
    return CertificateSignatory.objects.filter(is_active=True).order_by("name", "position", "pk")


def default_signatories() -> dict[str, CertificateSignatory | None]:
    """{slot: the person a new activity starts with, or None}."""
    rows = {s.default_slot: s for s in CertificateSignatory.objects.filter(default_slot__isnull=False)}
    return {slot: rows.get(slot) for slot in SLOTS}


def signatory_in_use(signatory: CertificateSignatory) -> bool:
    """True when a certificate uses this row: then it never changes (a change is a new version)."""
    return Certificate.objects.filter(_uses(signatory)).exists()


@transaction.atomic
def add_signatory(member: TBIMember, name: str, position: str, file: UploadedFile | None = None,
                  keep_background: bool = False) -> CertificateSignatory:
    """Add a person to the list (active), with their signature image (PNG or JPG; a white background is made
    transparent unless keep_background: prepare_signature_file) or without it (added later)."""
    require_permission(member, Permission.MANAGE_SIGNATORIES)
    name, position = _clean_person(name, position)
    if file is not None:
        file, _outcome = prepare_signature_file(file, keep_background)
    signatory = CertificateSignatory(name=name, position=position, is_active=True)
    if file is not None:
        signatory.signature_image_path.save(file.name, file, save=False)
    try:
        signatory.save()
    except Exception:
        _delete_file(signatory.signature_image_path)
        raise
    audit_logger.info("Signatory added: signatory_id=%s image=%s by member_id=%s", signatory.pk, file is not None, member.pk)
    return signatory


def deactivate_closed_reason(signatory: CertificateSignatory) -> str | None:
    """Why this person cannot be deactivated now, or None (an active default must be replaced first)."""
    if signatory.default_slot:
        return (f"{signatory.name} is the default {SLOT_LABELS[signatory.default_slot].lower()} signatory. "
                "Choose another default first.")
    return None


@transaction.atomic
def deactivate_signatory(member: TBIMember, signatory: CertificateSignatory) -> CertificateSignatory:
    """Remove a person from the dropdowns. Activities that already use them keep them ("(inactive)" on the tab)."""
    require_permission(member, Permission.MANAGE_SIGNATORIES)
    locked = CertificateSignatory.objects.select_for_update().get(pk=signatory.pk)
    if reason := deactivate_closed_reason(locked):
        raise CertificateError(reason)
    CertificateSignatory.objects.filter(pk=locked.pk).update(is_active=False)
    audit_logger.info("Signatory deactivated: signatory_id=%s by member_id=%s", locked.pk, member.pk)
    signatory.refresh_from_db()
    return signatory


@transaction.atomic
def set_default_signatories(member: TBIMember, left: CertificateSignatory, right: CertificateSignatory,
                            now: datetime | None = None) -> dict:
    """The people new activities start with (the Signatories page): two different, active people. The previous
    defaults are no longer defaults. A slot someone already chose never changes; EMPTY slots of activities without
    certificates are filled from the new defaults (fill_empty_slots_from_defaults, ASSUMPTIONS 366).
    Returns {"filled": slots filled}."""
    require_permission(member, Permission.MANAGE_SIGNATORIES)
    if left.pk == right.pk:
        raise CertificateError("The same person cannot be the default on both sides.")
    locked = {s.pk: s for s in CertificateSignatory.objects.select_for_update()
              .filter(Q(default_slot__isnull=False) | Q(pk__in=[left.pk, right.pk]))}
    for person in (locked[left.pk], locked[right.pk]):
        if not person.is_active:
            raise CertificateError(f"{person.name} is inactive and cannot be a default.")
    CertificateSignatory.objects.filter(default_slot__isnull=False).update(default_slot=None)
    CertificateSignatory.objects.filter(pk=left.pk).update(default_slot=Slot.LEFT)
    CertificateSignatory.objects.filter(pk=right.pk).update(default_slot=Slot.RIGHT)
    filled = fill_empty_slots_from_defaults(now)
    audit_logger.info("Default signatories: left=%s right=%s slots_filled=%s by member_id=%s", left.pk, right.pk, filled,
                      member.pk)
    return {"filled": filled}


def _uses(signatory: CertificateSignatory) -> Q:
    return Q(left_signatory=signatory) | Q(right_signatory=signatory)


def version_impact(signatory: CertificateSignatory) -> dict[str, int]:
    """What a new version of this person changes (the confirm dialog, ASSUMPTIONS 363): `activities` that use the
    row now, `not_issued` certificates that will use the new version (rendered again; Approved ones wait for approval
    again) and `issued` certificates that will become "Issued with an old signature" (they use this row on the side
    where their activity still has it)."""
    versions = [signatory.pk, *older_versions(signatory)]  # every activity on any version moves (ASSUMPTIONS 367)
    slots = ActivitySignatory.objects.filter(signatory_id__in=versions)
    uses = Q(left_signatory_id__in=versions) | Q(right_signatory_id__in=versions)
    still_current = Q(pk__in=[])
    for slot, field in SLOT_FIELDS.items():
        still_current |= Q(**{f"{field}_id__in": versions}) & Q(Exists(
            slots.filter(activity=OuterRef("activity_id"), slot=slot, signatory=OuterRef(f"{field}_id"))))
    moving = slots.values("activity")
    return {
        "activities": moving.distinct().count(),
        "not_issued": Certificate.objects.filter(activity__in=moving).filter(uses).exclude(status=Status.ISSUED).count(),
        "issued": Certificate.objects.filter(status=Status.ISSUED).filter(still_current).count(),
    }


def version_impact_text(impact: dict[str, int]) -> str:
    """ "This updates 3 activities. 12 certificates not issued yet will use the new version; 40 issued certificates
    will be marked 'Issued with an old signature'." """
    return (f"This updates {_plural(impact['activities'], 'activity', 'activities')}. "
            f"{_plural(impact['not_issued'], 'certificate', 'certificates')} not issued yet will use the new version; "
            f"{_plural(impact['issued'], 'issued certificate', 'issued certificates')} will be marked "
            "'Issued with an old signature'.")


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _new_version(member: TBIMember, old: CertificateSignatory, *, name: str, position: str, image_name: str | None,
                 now: datetime | None) -> tuple[CertificateSignatory, dict]:
    """Create the new version of a person (inside a transaction, `old` locked): a new row with the given values,
    active and default as the old one was (the old one is deactivated and loses its default); every activity that
    used the old row moves to the new one (one History row each); the certificates that are not Issued and used the
    old row are refreshed. Returns (new row, {"activities", "updated", "outdated", "outdated_activities"})."""
    from activities.services import write_signatory_history

    versions = [old.pk, *older_versions(old)]
    default_slot = old.default_slot
    CertificateSignatory.objects.filter(pk=old.pk).update(is_active=False, default_slot=None)
    new = CertificateSignatory(name=name, position=position, is_active=old.is_active, default_slot=default_slot,
                               previous_version=old)
    if image_name:
        new.signature_image_path.name = image_name
    new.save()

    # Every activity on ANY version of the person moves to the new one (ASSUMPTIONS 367), not only the old row's.
    slots = list(ActivitySignatory.objects.select_for_update(of=("self",)).filter(signatory_id__in=versions)
                 .select_related("activity", "signatory").order_by("activity_id", "slot"))
    moved = []
    for row in slots:
        if any(other.activity_id == row.activity_id for other in moved):
            continue  # two versions of the same person on one activity: only one slot can take the new row
        write_signatory_history(row.activity, member, SIGNATORY_HISTORY_LABELS[row.slot],
                                slot_text(row.signatory, row.position_on_certificate),
                                f"{slot_text(new, row.position_on_certificate)}, new version")
        moved.append(row)
    ActivitySignatory.objects.filter(pk__in=[row.pk for row in moved]).update(signatory=new)

    activities = {row.activity_id: row.activity for row in moved}
    cancelled = 0
    for activity in activities.values():  # what is printed changes: the approval is cancelled (ASSUMPTIONS 369)
        cancelled += cancel_certificate_approval(activity, member, now, f"new version of {name}")
    updated = sum(_sync_certificates(activity, member, now) for activity in activities.values())
    uses = Q(left_signatory_id__in=versions) | Q(right_signatory_id__in=versions)
    outdated = Certificate.objects.filter(uses).filter(outdated_q())
    return new, {
        "activities": len(activities),
        "updated": updated,
        "outdated": outdated.count(),
        "outdated_activities": outdated.values("activity").distinct().count(),
        "cancelled": cancelled,
    }


def older_versions(signatory: CertificateSignatory) -> list[int]:
    """The IDs of every earlier version of this person (previous_version, back to the first), newest first."""
    ids: list[int] = []
    previous = signatory.previous_version_id
    while previous is not None and previous not in ids and previous != signatory.pk:
        ids.append(previous)
        previous = CertificateSignatory.objects.filter(pk=previous).values_list("previous_version_id", flat=True).first()
    return ids


def current_version_of(signatory: CertificateSignatory) -> CertificateSignatory | None:
    """The active version of this person: the row itself when active, else the newest active row that follows it
    (next_versions, any number of steps); None when every later version is inactive too (ASSUMPTIONS 367)."""
    if signatory.is_active:
        return signatory
    seen, frontier, found = {signatory.pk}, [signatory.pk], []
    while frontier:
        rows = [row for row in CertificateSignatory.objects.filter(previous_version_id__in=frontier) if row.pk not in seen]
        seen.update(row.pk for row in rows)
        frontier = [row.pk for row in rows]
        found.extend(row for row in rows if row.is_active)
    return max(found, key=lambda row: row.pk, default=None)


@transaction.atomic
def edit_signatory(member: TBIMember, signatory: CertificateSignatory, name: str, position: str,
                   now: datetime | None = None) -> dict:
    """Change a person's name or position. Not used by any certificate yet: changed in place. Used: a new version
    (_new_version; the image stays). Returns {"action": "unchanged" | "edited" | "new_version", "signatory": ...,
    "activities", "updated", "outdated", "outdated_activities"}."""
    require_permission(member, Permission.MANAGE_SIGNATORIES)
    name, position = _clean_person(name, position)
    current = CertificateSignatory.objects.select_for_update().get(pk=signatory.pk)
    if not current.is_active:
        raise CertificateError("Inactive people (and earlier versions) cannot be edited.")
    empty = {"activities": 0, "updated": 0, "outdated": 0, "outdated_activities": 0}
    if (current.name, current.position) == (name, position):
        return {"action": "unchanged", "signatory": current, **empty}
    if not signatory_in_use(current):
        CertificateSignatory.objects.filter(pk=current.pk).update(name=name, position=position)
        current.refresh_from_db()
        audit_logger.info("Signatory edited in place: signatory_id=%s by member_id=%s", current.pk, member.pk)
        return {"action": "edited", "signatory": current, **empty}
    new, counts = _new_version(member, current, name=name, position=position,
                               image_name=current.signature_image_path.name or None, now=now)
    audit_logger.info("Signatory new version (name/position): signatory_id=%s -> %s certificates_updated=%s by member_id=%s",
                      current.pk, new.pk, counts["updated"], member.pk)
    return {"action": "new_version", "signatory": new, **counts}


@transaction.atomic
def replace_signature(member: TBIMember, signatory: CertificateSignatory, file: UploadedFile | None,
                      now: datetime | None = None, keep_background: bool = False) -> dict:
    """Add or replace a person's signature image (PNG or JPG; a white background is made transparent unless
    keep_background: prepare_signature_file). No image yet: stored on that row (every certificate using it gets it).
    Already an image: a new version with the new image (_new_version), so Issued certificates keep the old one.
    Returns {"action": "added" | "replaced", "signatory", "activities", "updated", "outdated", "outdated_activities"}."""
    require_permission(member, Permission.MANAGE_SIGNATORIES)
    current = CertificateSignatory.objects.select_for_update().get(pk=signatory.pk)
    if not current.is_active:
        raise CertificateError("Inactive people (and earlier versions) cannot get a new signature.")
    file, _outcome = prepare_signature_file(file, keep_background)
    field = CertificateSignatory._meta.get_field("signature_image_path")
    stored = None
    try:
        if not current.signature_image_path:
            current.signature_image_path.save(file.name, file, save=False)
            stored = current.signature_image_path.name
            current.save(update_fields=["signature_image_path"])
            impact = version_impact(current)
            result = {"action": "added", "signatory": current, "activities": impact["activities"],
                      "updated": impact["not_issued"], "outdated": 0, "outdated_activities": 0}
        else:
            stored = field.storage.save(field.generate_filename(current, file.name), file)
            new, counts = _new_version(member, current, name=current.name, position=current.position,
                                       image_name=stored, now=now)
            result = {"action": "replaced", "signatory": new, **counts}
    except BaseException:
        if stored:
            field.storage.delete(stored)
        raise
    audit_logger.info("Signature %s: signatory_id=%s -> %s certificates_updated=%s by member_id=%s", result["action"],
                      current.pk, result["signatory"].pk, result["updated"], member.pk)
    return result


def white_background_removable(signatory: CertificateSignatory) -> bool:
    """True when the person's stored image has no transparency yet ("Remove white background" is offered)."""
    if not signatory.is_active or not signatory.signature_image_path:
        return False
    try:
        with signatory.signature_image_path.open("rb") as handle, Image.open(handle) as image:
            image.load()
            return not imaging.has_transparency(image)
    except (FileNotFoundError, OSError, ValueError):
        return False


@transaction.atomic
def remove_signature_background(member: TBIMember, signatory: CertificateSignatory, now: datetime | None = None) -> dict:
    """The Signatories page's "Remove white background" for an existing image (ASSUMPTIONS 366): a new version of
    the person with the cleaned PNG (like Replace: _new_version), so Issued certificates keep the old image. Refused
    for an image that is already transparent. Returns replace_signature's dict with action "background_removed"."""
    require_permission(member, Permission.MANAGE_SIGNATORIES)
    current = CertificateSignatory.objects.select_for_update().get(pk=signatory.pk)
    if not current.is_active:
        raise CertificateError("Inactive people (and earlier versions) cannot get a new signature.")
    if not signature_present(current):
        raise CertificateError(f"{current.name} has no signature image yet.")
    with current.signature_image_path.open("rb") as handle, Image.open(handle) as image:
        image.load()
        if imaging.has_transparency(image):
            raise CertificateError("This signature already has a transparent background.")
        cleaned = imaging.remove_white_background(image)
    content = ContentFile(imaging.png_bytes(cleaned), name="signature.png")
    field = CertificateSignatory._meta.get_field("signature_image_path")
    stored = field.storage.save(field.generate_filename(current, content.name), content)
    try:
        new, counts = _new_version(member, current, name=current.name, position=current.position, image_name=stored,
                                   now=now)
    except BaseException:
        field.storage.delete(stored)
        raise
    audit_logger.info("Signature background removed: signatory_id=%s -> %s certificates_updated=%s by member_id=%s",
                      current.pk, new.pk, counts["updated"], member.pk)
    return {"action": "background_removed", "signatory": new, **counts}


# --- An activity's two signatories and their titles (ACTIVITY_SIGNATORY; ASSUMPTIONS 363, 365-367) ---------------


def activity_slots(activity: Activity) -> dict[str, ActivitySignatory | None]:
    """{slot: the saved ACTIVITY_SIGNATORY row (with its signatory), or None}."""
    rows = {row.slot: row for row in ActivitySignatory.objects.filter(activity=activity).select_related("signatory")}
    return {slot: rows.get(slot) for slot in SLOTS}


def activity_signatories(activity: Activity) -> dict[str, CertificateSignatory | None]:
    """{slot: who signs this activity's certificates, or None}. Only what is saved (ASSUMPTIONS 366): a new activity
    starts with the defaults; a slot without a default stays empty until someone chooses or a default is set
    (fill_empty_slots_from_defaults). It is never filled quietly when certificates are created."""
    return {slot: row.signatory if row else None for slot, row in activity_slots(activity).items()}


saved_activity_signatories = activity_signatories  # the same since ASSUMPTIONS 366 (no default fallback)


def printed_title(row: ActivitySignatory | None) -> str:
    """The title printed under the signature: the activity's "Title on this certificate", else the person's usual
    position (ASSUMPTIONS 365)."""
    if row is None:
        return ""
    return row.position_on_certificate or row.signatory.position


def slot_text(person: CertificateSignatory | None, title: str | None = None) -> str:
    """ "Dr. Maria Santos (University President)" with the title printed for the activity; "" for nobody (History)."""
    if person is None:
        return ""
    return f"{person.name} ({title or person.position})"


def _clean_title(title: str | None, field: str) -> str | None:
    """The "Title on this certificate" text: spaces trimmed; empty = None (the person's position prints)."""
    title = (title or "").strip()
    if len(title) > 255:
        raise SlotError("The title can be at most 255 characters.", field)
    return title or None


def _other(slot: str) -> str:
    return Slot.RIGHT if slot == Slot.LEFT else Slot.LEFT


def missing_slots(signatories: dict[str, CertificateSignatory | None]) -> list[str]:
    """The slots nobody fills yet, left first ([] when both are chosen)."""
    return [slot for slot in SLOTS if signatories[slot] is None]


def missing_slots_reason(signatories: dict[str, CertificateSignatory | None]) -> str | None:
    if missing_slots(signatories):
        return "Choose the two signatories on the Certificates tab first."
    return None


def can_choose_signatories(member: TBIMember) -> bool:
    """Who may change an activity's signatories and titles: can_create_activity or can_approve_certificates
    (ASSUMPTIONS 363, 365). Without either flag: view only."""
    return has_permission(member, Permission.CREATE_ACTIVITY) or has_permission(member, Permission.APPROVE_CERTIFICATES)


def assign_default_signatories(activity: Activity, chosen: dict | None = None) -> None:
    """A new activity starts with the default signatories (activities.services.save_new_activity, same transaction).
    `chosen` (the New activity form's "Who signs the certificates", pre-filled with the defaults): {slot: (person or
    None, title)}; a slot without a choice takes the default. A slot with no default and no choice stays empty (Needs
    attention: "Choose who signs the certificates"). No History row (the activity's first row is its creation).
    Refused as ValidationError({field: message}) so the form shows it under the field."""
    defaults = default_signatories()
    wanted: dict[str, tuple[CertificateSignatory, str | None]] = {}
    try:
        for slot in SLOTS:
            person, title = (chosen or {}).get(slot) or (None, None)
            if person is None:
                default = defaults[slot]
                if default is None or not default.is_active:
                    continue
                person = default
            elif not person.is_active:
                raise SlotError(f"{person.name} is inactive. Choose an active person.", f"signatory_{slot}")
            wanted[slot] = (person, _clean_title(title, f"title_{slot}"))
        if len(wanted) == 2 and wanted[Slot.LEFT][0].pk == wanted[Slot.RIGHT][0].pk:
            raise SlotError("The same person cannot sign on both sides. Choose another person.", "signatory_right")
    except SlotError as error:
        raise ValidationError({error.field: [str(error)]}) from error
    for slot, (person, title) in wanted.items():
        ActivitySignatory.objects.create(activity=activity, slot=slot, signatory=person, position_on_certificate=title)


@transaction.atomic
def fill_empty_slots_from_defaults(now: datetime | None = None) -> int:
    """The system sticks to the defaults (owner decision, ASSUMPTIONS 366): when a default is set or changed, every
    EMPTY slot of an activity that has no certificates yet gets the new default. A slot someone already chose is never
    replaced and activities with certificates are never touched. One History row by System per filled slot. Returns
    how many slots were filled."""
    from activities.services import write_signatory_history

    defaults = default_signatories()
    has_certificates = Certificate.objects.filter(activity=OuterRef("pk"))
    filled = 0
    for slot in SLOTS:
        person = defaults[slot]
        if person is None or not person.is_active:
            continue
        activities = (Activity.objects.select_for_update(of=("self",)).filter(~Exists(has_certificates))
                      .exclude(signatory_slots__slot=slot).exclude(signatory_slots__signatory=person).order_by("pk"))
        for activity in activities:
            ActivitySignatory.objects.create(activity=activity, slot=slot, signatory=person)
            write_signatory_history(activity, None, SIGNATORY_HISTORY_LABELS[slot], "", f"{slot_text(person)}, default")
            filled += 1
    return filled


def _sync_certificates(activity: Activity, member: TBIMember | None, now: datetime | None) -> int:
    """Put the activity's current signatories and printed titles on its certificates that are not Issued and differ
    (_refresh_certificates: the PDF is rendered again, Approved ones wait for approval again). Returns how many."""
    slots = activity_slots(activity)
    if any(row is None for row in slots.values()):
        return 0
    wanted_ids = {SLOT_FIELDS[slot]: slots[slot].signatory_id for slot in SLOTS}
    wanted_titles = {f"{SLOT_FIELDS[slot]}_title": printed_title(slots[slot]) for slot in SLOTS}
    certificates = list(Certificate.objects.select_for_update(of=("self",)).filter(activity=activity)
                        .exclude(status=Status.ISSUED).order_by("pk"))
    stale = [c for c in certificates
             if any(getattr(c, f"{field}_id") != pk for field, pk in wanted_ids.items())
             or any(getattr(c, field) != title for field, title in wanted_titles.items())]
    changes = {**{field: slots[slot].signatory for slot, field in SLOT_FIELDS.items()}, **wanted_titles}
    _refresh_certificates(stale, member, now, **changes)
    return len(stale)


def _apply_slots(member: TBIMember, activity: Activity, wanted: dict[str, tuple[CertificateSignatory | None, str | None]],
                 now: datetime | None) -> dict:
    """Set the given slots ({slot: (person, title or None)}) of a LOCKED activity. A slot is never emptied (person None
    = leave the slot as it is), a newly chosen person must be active, and the same person never signs on both
    sides. One History row per changed slot ("Certificate signatory (left)": "Name (title)" -> "Name (title)"); the
    certificates that are not Issued then use the new person / title (_sync_certificates). Returns {"changed",
    "updated", "outdated"} (outdated = this activity's Issued certificates with an old signature or title)."""
    from activities.services import write_signatory_history

    current = activity_slots(activity)
    final = {slot: (row.signatory, row.position_on_certificate) if row else (None, None) for slot, row in current.items()}
    changed: dict[str, tuple[CertificateSignatory | None, str | None]] = {}
    for slot, (person, title) in wanted.items():
        old_person, old_title = final[slot]
        if person is None:  # nothing chosen = no change: a slot is never emptied (the form offers no empty option)
            continue
        same_person = old_person is not None and old_person.pk == person.pk
        if not same_person and not person.is_active:
            raise SlotError(f"{person.name} is inactive. Choose an active person.", f"signatory_{slot}")
        if same_person and title == old_title:
            continue
        final[slot] = (person, title)
        changed[slot] = (old_person, old_title)
    left, right = final[Slot.LEFT][0], final[Slot.RIGHT][0]
    if left is not None and right is not None and left.pk == right.pk:
        slot = Slot.RIGHT if Slot.RIGHT in changed else Slot.LEFT
        raise SlotError(f"{left.name} already signs on the {SLOT_LABELS[_other(slot)].lower()}. Choose another person.",
                        f"signatory_{slot}")
    if not changed:
        return {"changed": False, "updated": 0, "outdated": outdated_certificates(activity).count()}
    reasons = []
    for slot, (old_person, _old_title) in changed.items():
        same = old_person is not None and old_person.pk == final[slot][0].pk
        reasons.append(f"{SLOT_LABELS[slot].lower()} {'title' if same else 'signatory'} changed")
    cancelled = cancel_certificate_approval(activity, member, now, ", ".join(reasons))  # ASSUMPTIONS 369
    # Delete first, then create: a swap (left <-> right) would otherwise break unique (activity, signatory).
    ActivitySignatory.objects.filter(activity=activity, slot__in=list(changed)).delete()
    for slot, (old_person, old_title) in changed.items():
        person, title = final[slot]
        ActivitySignatory.objects.create(activity=activity, slot=slot, signatory=person, position_on_certificate=title)
        write_signatory_history(activity, member, SIGNATORY_HISTORY_LABELS[slot], slot_text(old_person, old_title),
                                slot_text(person, title))
    updated = _sync_certificates(activity, member, now)
    audit_logger.info("Activity signatories: activity_id=%s slots=%s certificates_updated=%s by member_id=%s",
                      activity.pk, ",".join(f"{slot}={final[slot][0].pk}" for slot in changed), updated, member.pk)
    return {"changed": True, "updated": updated, "outdated": outdated_certificates(activity).count(),
            "cancelled": int(cancelled)}


_KEEP = object()  # set_activity_signatory: keep the title when the person stays the same


@transaction.atomic
def set_activity_signatory(member: TBIMember, activity: Activity, slot: str, signatory: CertificateSignatory,
                           now: datetime | None = None, *, title=_KEEP) -> dict:
    """Choose who signs on `slot` for this activity and the title printed for them (the Certificates tab; any status:
    certificates exist only from Conducted on). can_create_activity or can_approve_certificates.
    `title`: the "Title on this certificate" text ("" = the person's position); left out = kept for the same person,
    cleared for another person. Refusals (SlotError, a CertificateError): an inactive person, the person already on
    the other side. Returns {"changed", "updated", "outdated"} (_apply_slots)."""
    require_any_permission(member, Permission.CREATE_ACTIVITY, Permission.APPROVE_CERTIFICATES)
    if slot not in SLOTS:
        raise ValidationError({"slot": ["Choose left or right."]})
    locked = Activity.objects.select_for_update(of=("self",)).get(pk=activity.pk)
    person = CertificateSignatory.objects.get(pk=signatory.pk)
    row = activity_slots(locked)[slot]
    if title is _KEEP:
        title = row.position_on_certificate if row is not None and row.signatory_id == person.pk else None
    else:
        title = _clean_title(title, f"title_{slot}")
    return _apply_slots(member, locked, {slot: (person, title)}, now)


@transaction.atomic
def set_activity_signatories(member: TBIMember, activity: Activity, choices: dict, now: datetime | None = None) -> dict:
    """The activity edit form's "Who signs the certificates": {slot: (person or None, title text)} for both slots,
    saved inside the caller's transaction (activities.services.update_activity). Same rules as
    set_activity_signatory; refusals as ValidationError({"signatory_left": [...]}) for the form's fields."""
    require_any_permission(member, Permission.CREATE_ACTIVITY, Permission.APPROVE_CERTIFICATES)
    locked = Activity.objects.select_for_update(of=("self",)).get(pk=activity.pk)
    try:
        wanted = {slot: (person, _clean_title(title, f"title_{slot}")) for slot, (person, title) in choices.items()
                  if slot in SLOTS}
        return _apply_slots(member, locked, wanted, now)
    except SlotError as error:
        raise ValidationError({error.field: [str(error)]}) from error


def use_current_version(member: TBIMember, activity: Activity, slot: str, now: datetime | None = None) -> dict:
    """The Signatures section's "Use the current version" (ASSUMPTIONS 367): the slot's inactive person is switched
    to the newest active version of the same person (current_version_of), keeping the title. Same permission and
    refresh rules as set_activity_signatory. Refused when there is no newer active version."""
    require_any_permission(member, Permission.CREATE_ACTIVITY, Permission.APPROVE_CERTIFICATES)
    if slot not in SLOTS:
        raise ValidationError({"slot": ["Choose left or right."]})
    row = activity_slots(activity)[slot]
    if row is None:
        raise CertificateError(missing_slots_reason(activity_signatories(activity)))
    newer = current_version_of(row.signatory)
    if newer is None or newer.pk == row.signatory_id:
        raise CertificateError(f"There is no newer active version of {row.signatory.name}. Choose another person.")
    return set_activity_signatory(member, activity, slot, newer, now, title=row.position_on_certificate or "")


def inactive_slots(activity: Activity) -> dict[str, CertificateSignatory | None]:
    """{slot: the newest active version of the slot's INACTIVE person, or None when there is none} for the slots whose
    person is inactive (the amber note; ASSUMPTIONS 367). {} when everyone is active."""
    return {slot: current_version_of(row.signatory) for slot, row in activity_slots(activity).items()
            if row is not None and not row.signatory.is_active}


def active_template() -> CertificateTemplate | None:
    return CertificateTemplate.objects.filter(is_active=True).first()


NO_TEMPLATE = ("Cannot create certificates: there is no active certificate design. "
               "The System Administrator adds one in the Admin Console first.")


def design_fields(activity: Activity) -> dict:
    """The CERTIFICATE columns of the current design for `activity`: the active template, the activity's left and
    right signatories and the titles printed for them (printed_title). Refused, naming what is missing."""
    template = active_template()
    if template is None:
        raise CertificateError(NO_TEMPLATE)
    slots = activity_slots(activity)
    if reason := missing_slots_reason({slot: row.signatory if row else None for slot, row in slots.items()}):
        raise CertificateError(reason)
    return {
        "certificate_template": template,
        "left_signatory": slots[Slot.LEFT].signatory,
        "right_signatory": slots[Slot.RIGHT].signatory,
        "left_signatory_title": printed_title(slots[Slot.LEFT]),
        "right_signatory_title": printed_title(slots[Slot.RIGHT]),
    }


def active_design(activity: Activity) -> tuple[CertificateTemplate, CertificateSignatory, CertificateSignatory]:
    """(template, left, right) for `activity` (design_fields)."""
    fields = design_fields(activity)
    return fields["certificate_template"], fields["left_signatory"], fields["right_signatory"]


# --- The certificate text (CERTIFICATE_TEXT; ASSUMPTIONS 370) ---------------------------------------------------------

TEXT_HISTORY_LABEL = "Certificate text"
TEXT_TYPES = (TEXT_PARTICIPATION, TEXT_APPRECIATION)
TEXT_TYPE_LABELS = {TEXT_PARTICIPATION: "Participation", TEXT_APPRECIATION: "Appreciation"}
RECIPIENT_FOR_TEXT = {TEXT_PARTICIPATION: RecipientType.REGISTRATION, TEXT_APPRECIATION: RecipientType.RESOURCE_PERSON}
SAMPLE_NAMES = {TEXT_PARTICIPATION: "Juan Dela Cruz", TEXT_APPRECIATION: "Dr. Maria Santos"}
SAMPLE_TOPIC = "Building a Startup Pitch"


def text_values(row) -> dict[str, str]:
    """The four fields of a CERTIFICATE_TEXT row as a dict."""
    return {field: getattr(row, field) or "" for field in wording.FIELDS}


def current_texts(activity: Activity | None = None) -> dict[str, CertificateText]:
    """{type: the wording new certificates get}: the activity's custom text, else the default (activity None: the
    defaults)."""
    return {kind: wording.current_text_for(activity.pk if activity else None, kind) for kind in TEXT_TYPES}


def uses_custom_text(activity: Activity) -> bool:
    """True when the activity has its own current text for at least one type ("Custom text for this activity")."""
    return CertificateText.objects.filter(activity=activity, is_current=True).exists()


def can_edit_activity_text(member: TBIMember) -> bool:
    """Who may choose an activity's wording: can_create_activity or can_approve_certificates (without either: view only)."""
    return can_choose_signatories(member)


def _check_type(certificate_type: str) -> None:
    if certificate_type not in TEXT_TYPES:
        raise ValidationError({"certificate_type": ["Choose Participation or Appreciation."]})


def clean_texts(texts: dict[str, dict]) -> dict[str, dict]:
    """The posted wording per type, checked (certificates.wording.clean). Refused as ValidationError with one message per
    field, keyed "<type>-<field>" (e.g. "participation-main_sentence") so the form shows it under the field."""
    cleaned, errors = {}, {}
    for kind, values in texts.items():
        _check_type(kind)
        cleaned[kind], problems = wording.clean(values, kind)
        errors.update({f"{kind}-{field}": [message] for field, message in problems.items()})
    if errors:
        raise ValidationError(errors)
    return cleaned


def _sync_text(activity_id: int, certificate_type: str, row: CertificateText, member: TBIMember | None,
               now: datetime | None) -> int:
    """Put `row` on the activity's certificates of that type that are not Issued and use another wording
    (_refresh_certificates: the PDF is rendered again). Returns how many."""
    stale = list(Certificate.objects.select_for_update(of=("self",))
                 .filter(activity_id=activity_id, recipient_type=RECIPIENT_FOR_TEXT[certificate_type])
                 .exclude(status=Status.ISSUED).exclude(certificate_text=row).order_by("pk"))
    _refresh_certificates(stale, member, now, certificate_text=row)
    return len(stale)


def _on_default(certificate_type: str):
    """Activities that use the default wording of the type (no current custom text) and have a certificate of that
    type that is not Issued: the ones a change of the default updates."""
    custom = CertificateText.objects.filter(activity=OuterRef("pk"), certificate_type=certificate_type, is_current=True)
    waiting = Certificate.objects.filter(activity=OuterRef("pk"), recipient_type=RECIPIENT_FOR_TEXT[certificate_type]
                                         ).exclude(status=Status.ISSUED)
    return Activity.objects.filter(Exists(waiting)).exclude(Exists(custom)).order_by("pk")


def default_text_impact(certificate_type: str) -> dict[str, int]:
    """What saving a new default wording changes (the confirm dialog): `activities` using the default with certificates
    not Issued, `certificates` (rendered again) and `approved` activities whose approval is cancelled."""
    _check_type(certificate_type)
    activities = _on_default(certificate_type)
    custom = CertificateText.objects.filter(activity=OuterRef("activity_id"), certificate_type=certificate_type,
                                            is_current=True)
    return {
        "activities": activities.count(),
        "certificates": (Certificate.objects.filter(recipient_type=RECIPIENT_FOR_TEXT[certificate_type])
                         .exclude(status=Status.ISSUED).exclude(Exists(custom)).count()),
        "approved": activities.filter(certificates_approved_at__isnull=False).count(),
    }


def default_text_impact_text(impact: dict[str, int]) -> str:
    """ "This updates 2 activities: 14 certificates not issued yet are rendered again with the new text; the approval of
    1 activity is cancelled. Issued certificates keep the text they were sent with." """
    if not impact["activities"]:
        return "No certificate waiting to be issued uses this text now; new certificates will use the new text."
    text = (f"This updates {_plural(impact['activities'], 'activity', 'activities')}: "
            f"{_plural(impact['certificates'], 'certificate', 'certificates')} not issued yet "
            f"{'is' if impact['certificates'] == 1 else 'are'} rendered again with the new text")
    if impact["approved"]:
        text += f"; the approval of {_plural(impact['approved'], 'activity', 'activities')} is cancelled"
    return text + ". Issued certificates keep the text they were sent with."


@transaction.atomic
def save_default_text(member: TBIMember, certificate_type: str, values: dict, now: datetime | None = None) -> dict:
    """The "Certificate text" page (can_approve_certificates): a new default wording for one type. Unchanged text saves
    nothing. Otherwise a new row (the old one is no longer current) and every activity that uses the default and has
    certificates of that type not Issued gets it: its approval is cancelled (cancel_certificate_approval, History) and
    those certificates are rendered again; one History row "Certificate text" per activity. Issued certificates keep
    their row. Returns {"changed", "activities", "updated", "cancelled"}."""
    from activities.services import write_certificate_text_history

    require_permission(member, Permission.APPROVE_CERTIFICATES)
    _check_type(certificate_type)
    cleaned = clean_texts({certificate_type: values})[certificate_type]
    current = wording.default_text(certificate_type)
    current = CertificateText.objects.select_for_update().get(pk=current.pk)
    if text_values(current) == cleaned:
        return {"changed": False, "activities": 0, "updated": 0, "cancelled": 0}
    CertificateText.objects.filter(pk=current.pk).update(is_current=False)
    new = CertificateText.objects.create(certificate_type=certificate_type, created_by=member, **cleaned)
    label = TEXT_TYPE_LABELS[certificate_type]
    activities = list(_on_default(certificate_type).select_for_update(of=("self",)))
    updated = cancelled = 0
    for activity in activities:
        cancelled += cancel_certificate_approval(activity, member, now, f"default {label} text changed")
        updated += _sync_text(activity.pk, certificate_type, new, member, now)
        write_certificate_text_history(activity, member, f"Default text ({label})", f"Default text ({label}), changed")
    audit_logger.info("Default certificate text: type=%s text_id=%s -> %s activities=%s certificates_updated=%s "
                      "by member_id=%s", certificate_type, current.pk, new.pk, len(activities), updated, member.pk)
    return {"changed": True, "activities": len(activities), "updated": updated, "cancelled": cancelled}


@transaction.atomic
def save_activity_text(member: TBIMember, activity: Activity, texts: dict[str, dict], now: datetime | None = None) -> dict:
    """The Certificates tab's "Custom text for this activity" (can_create_activity or can_approve_certificates):
    {type: {field: text}} for one or both types. A type whose text is the same as what the activity prints now is left
    alone; a changed one gets a new row for the activity. Then the approval is cancelled (History) and the
    certificates of the changed types that are not Issued are rendered again; one History row "Certificate text".
    Refused (ValidationError, fields "<type>-<field>") with the wording's own checks.
    Returns {"changed", "updated", "cancelled"}."""
    from activities.services import write_certificate_text_history

    require_any_permission(member, Permission.CREATE_ACTIVITY, Permission.APPROVE_CERTIFICATES)
    locked = Activity.objects.select_for_update(of=("self",)).get(pk=activity.pk)
    cleaned = clean_texts(texts)
    was_custom = uses_custom_text(locked)
    changed = []
    for kind in TEXT_TYPES:
        if kind not in cleaned:
            continue
        current = wording.current_text_for(locked.pk, kind)
        if text_values(current) == cleaned[kind]:
            continue  # the same words: nothing to do (the default stays the default)
        CertificateText.objects.filter(activity=locked, certificate_type=kind, is_current=True).update(is_current=False)
        CertificateText.objects.create(certificate_type=kind, activity=locked, created_by=member, **cleaned[kind])
        changed.append(kind)
    if not changed:
        return {"changed": False, "updated": 0, "cancelled": 0}
    cancelled = cancel_certificate_approval(locked, member, now, "certificate text changed")
    updated = sum(_sync_text(locked.pk, kind, wording.current_text_for(locked.pk, kind), member, now) for kind in changed)
    labels = ", ".join(TEXT_TYPE_LABELS[kind] for kind in changed)
    write_certificate_text_history(locked, member, "Custom text" if was_custom else "Default text",
                                   f"Custom text ({labels})")
    audit_logger.info("Activity certificate text: activity_id=%s types=%s certificates_updated=%s by member_id=%s",
                      locked.pk, ",".join(changed), updated, member.pk)
    return {"changed": True, "updated": updated, "cancelled": cancelled}


@transaction.atomic
def use_default_text(member: TBIMember, activity: Activity, now: datetime | None = None) -> dict:
    """The Certificates tab's "Use the default text": the activity's custom text is no longer current, its certificates
    that are not Issued get the default again (rendered again) and the approval is cancelled; one History row. Nothing
    happens when the activity already uses the default. Returns {"changed", "updated", "cancelled"}."""
    from activities.services import write_certificate_text_history

    require_any_permission(member, Permission.CREATE_ACTIVITY, Permission.APPROVE_CERTIFICATES)
    locked = Activity.objects.select_for_update(of=("self",)).get(pk=activity.pk)
    custom = CertificateText.objects.filter(activity=locked, is_current=True)
    kinds = sorted(set(custom.values_list("certificate_type", flat=True)), key=TEXT_TYPES.index)
    if not kinds:
        return {"changed": False, "updated": 0, "cancelled": 0}
    custom.update(is_current=False)
    cancelled = cancel_certificate_approval(locked, member, now, "certificate text changed")
    updated = sum(_sync_text(locked.pk, kind, wording.default_text(kind), member, now) for kind in kinds)
    write_certificate_text_history(locked, member, "Custom text", "Default text")
    audit_logger.info("Activity certificate text: activity_id=%s back to the default, certificates_updated=%s by "
                      "member_id=%s", locked.pk, updated, member.pk)
    return {"changed": True, "updated": updated, "cancelled": cancelled}


def preview_values(activity: Activity | None, certificate_type: str) -> tuple[dict[str, str], str]:
    """(placeholder values, where they come from) for the live preview of the wording: the activity's first certificate
    of that type (its printed name), else sample names with the activity's own details; no activity (the default text
    page): an invented sample activity."""
    _check_type(certificate_type)
    if activity is None:
        today = timezone.localdate()
        sample = Activity(title="Design Thinking Workshop", start_date=today, end_date=today, venue="CSU Main Campus")
        values = pdf.placeholder_values(Certificate(recipient_type=RECIPIENT_FOR_TEXT[certificate_type]),
                                        SAMPLE_NAMES[certificate_type], activity=sample)
        values["topic"] = SAMPLE_TOPIC if certificate_type == TEXT_APPRECIATION else ""
        return values, "Sample values (an invented activity)."
    certificate = (Certificate.objects.filter(activity=activity, recipient_type=RECIPIENT_FOR_TEXT[certificate_type])
                   .select_related("activity", "registration", "resource_person").order_by("certificate_number").first())
    if certificate is not None:
        name, _final = preview_name(certificate)
        return (pdf.placeholder_values(certificate, name),
                f"Filled in for {certificate.certificate_number} ({name}).")
    probe = Certificate(activity=activity, recipient_type=RECIPIENT_FOR_TEXT[certificate_type])
    name = SAMPLE_NAMES[certificate_type]
    if certificate_type == TEXT_APPRECIATION:
        link = activity.resource_person_links.select_related("resource_person").order_by("pk").first()
        if link is not None:
            probe.resource_person = link.resource_person
            name = link.resource_person.name
    return pdf.placeholder_values(probe, name), "Filled in with this activity's details and a sample name."


def wording_preview(activity: Activity | None, certificate_type: str, values: dict) -> dict:
    """The live preview of typed wording (nothing is saved): {"text": the four filled lines or None, "errors":
    {field: message}, "source": where the values come from}. The errors are the ones saving would give."""
    _check_type(certificate_type)
    cleaned, errors = wording.clean(values, certificate_type)
    filled, source = preview_values(activity, certificate_type)
    text = None if errors else wording.fill_all(cleaned, filled)
    return {"text": text, "errors": errors, "source": source}


# --- Certificate numbers ------------------------------------------------------------------------


def _certificate_numbers(now: datetime | None = None):
    """Yield TBI-<Manila year>-<5-digit sequence> numbers, restarting at 00001 each year.

    Must run inside a transaction: a PostgreSQL advisory lock (held until commit) makes other
    generations wait, so two generations at the same moment never get the same number. The
    unique constraint on certificate_number is the final guard.

    A number is never given twice (ASSUMPTIONS 389): CERTIFICATE_NUMBER_SEQUENCE keeps the last number given per year,
    so a deleted certificate (its activity deleted, attendance undone) leaves a gap instead of being reused. The highest
    existing number still counts, in case the sequence row is missing or behind.
    """
    year = timezone.localtime(now or timezone.now()).year
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [NUMBER_LOCK_KEY])
    prefix = f"TBI-{year}-"
    sequence, _ = CertificateNumberSequence.objects.select_for_update().get_or_create(year=year)
    existing = Certificate.objects.filter(certificate_number__startswith=prefix).values_list("certificate_number", flat=True)
    last = max([sequence.last_number] + [int(n[len(prefix):]) for n in existing if n[len(prefix):].isdigit()])
    while True:
        last += 1
        CertificateNumberSequence.objects.filter(year=year).update(last_number=last)  # same transaction
        yield f"{prefix}{last:05d}"


# --- Generating: automatic, by the System (ASSUMPTIONS 362) ---------------------------------------


NOT_CONDUCTED = "Certificates are created automatically when the activity is Conducted."


def generation_closed_reason(activity: Activity) -> str | None:
    """None when the missing certificates of `activity` can be created now; otherwise why not (not Conducted, no active
    design, or the activity's two signatories not chosen: active_design()). Read only: the Certificates tab and Needs
    attention show it, and generate_missing_certificates refuses with the same message."""
    if activity.status.name != ActivityStatus.CONDUCTED:
        return NOT_CONDUCTED
    try:
        active_design(activity)
    except CertificateError as error:
        return str(error)
    return None


def _missing_recipients(activity: Activity, registration_ids: list[int] | None = None):
    """(registrations, resource person links) of `activity` that should have a certificate and do not yet."""
    registrations = activity.registrations.filter(attendance_confirmed=True, certificates__isnull=True)
    has_certificate = Certificate.objects.filter(activity=activity, resource_person__isnull=False).values("resource_person")
    links = activity.resource_person_links.exclude(resource_person__in=has_certificate)
    if registration_ids is not None:  # attendance marked after Conducted: only these people
        registrations = registrations.filter(pk__in=registration_ids)
        links = links.none()
    return (registrations.order_by("name", "pk"),
            links.select_related("resource_person").order_by("resource_person__name", "pk"))


def created_text(participation: int, appreciation: int) -> str:
    """The History row's text: "3 created automatically (2 Participation, 1 Appreciation)"."""
    parts = [f"{n} {label}" for n, label in ((participation, "Participation"), (appreciation, "Appreciation")) if n]
    return f"{participation + appreciation} created automatically ({', '.join(parts)})"


@transaction.atomic
def generate_missing_certificates(activity: Activity, *, registration_ids: list[int] | None = None,
                                  now: datetime | None = None) -> dict[str, int]:
    """Create the missing certificates of a Conducted activity, by the System (no member). Safe to run any number of
    times: the activity row is locked and the unique constraints allow one certificate per registration and per
    resource person per activity.

    - One Certificate of Participation (email) per registration with attendance confirmed.
    - One Certificate of Appreciation (hard copy) per linked resource person.
    `registration_ids`: only these attendees (attendance marked after the activity was Conducted).
    Each records the active template, the activity's left and right signatories and their printed titles
    (design_fields; both slots must be filled, ASSUMPTIONS 366). One History row by System
    ("Certificates: 3 created automatically (...)") when something was created. Refused (CertificateError) when the
    activity is not Conducted or the design is incomplete (generation_closed_reason)."""
    from activities.services import write_certificate_history

    locked = Activity.objects.select_for_update(of=("self",)).select_related("status").get(pk=activity.pk)
    if reason := generation_closed_reason(locked):
        raise CertificateError(reason)
    registrations, links = (list(rows) for rows in _missing_recipients(locked, registration_ids))
    participation = appreciation = 0
    if registrations or links:
        numbers = _certificate_numbers(now)
        common = {"activity": locked, **design_fields(locked)}
        texts = {kind: wording.current_text_for(locked.pk, kind) for kind in CertificateText.CertificateType.values}
        for registration in registrations:
            Certificate.objects.create(
                certificate_number=next(numbers), recipient_type=RecipientType.REGISTRATION, registration=registration,
                delivery_method=DeliveryMethod.EMAIL, certificate_text=texts[TEXT_PARTICIPATION], **common,
            )
            participation += 1
        for link in links:
            Certificate.objects.create(
                certificate_number=next(numbers), recipient_type=RecipientType.RESOURCE_PERSON,
                resource_person=link.resource_person, delivery_method=DeliveryMethod.HARD_COPY,
                certificate_text=texts[TEXT_APPRECIATION], **common,
            )
            appreciation += 1
        write_certificate_history(locked, None, "", created_text(participation, appreciation))
        # An approved activity: the new ones follow the same rule (ASSUMPTIONS 369), e.g. attendance marked later.
        approve_ready_certificates(locked, now=now)
    return {
        "participation_created": participation,
        "appreciation_created": appreciation,
        "total": Certificate.objects.filter(activity=locked).count(),
    }


def auto_generate_certificates(activity: Activity, *, registration_ids: list[int] | None = None,
                               now: datetime | None = None) -> int:
    """generate_missing_certificates for the automatic callers (the move to Conducted, attendance marked after it, the
    5-minute job). A refusal (incomplete design) never stops the caller: nothing is created, the Certificates tab and
    Needs attention show the reason, and the 5-minute job tries again. Returns how many were created."""
    try:
        with transaction.atomic():  # a savepoint: a refusal leaves the caller's transaction usable
            result = generate_missing_certificates(activity, registration_ids=registration_ids, now=now)
    except CertificateError:
        return 0
    return result["participation_created"] + result["appreciation_created"]


def activities_missing_certificates() -> QuerySet:
    """Conducted activities with an attendee or a resource person who has no certificate yet (the 5-minute job's
    catch-up: the design was incomplete at the move, or a run was interrupted)."""
    attendee_without = Registration.objects.filter(
        activity=OuterRef("pk"), attendance_confirmed=True, certificates__isnull=True)
    speaker_without = ActivityResourcePerson.objects.filter(activity=OuterRef("pk")).exclude(
        resource_person__in=Certificate.objects.filter(activity=OuterRef(OuterRef("pk")), resource_person__isnull=False)
        .values("resource_person"))
    return (Activity.objects.filter(status__name=ActivityStatus.CONDUCTED)
            .filter(Exists(attendee_without) | Exists(speaker_without)).order_by("pk"))


def generate_pending_certificates(now: datetime | None = None) -> int:
    """The 5-minute job: create the missing certificates of every Conducted activity (auto_generate_certificates).
    Returns how many certificates were created."""
    return sum(auto_generate_certificates(activity, now=now) for activity in activities_missing_certificates())


# --- Status rules ---------------------------------------------------------------------------------


def get_submitted_evaluation(certificate: Certificate) -> EvaluationSubmission | None:
    """Return the participant's submitted evaluation, or None if there isn't one."""
    if certificate.evaluation_submission_id:
        return certificate.evaluation_submission
    if certificate.registration_id is None:
        return None
    return EvaluationSubmission.objects.filter(registration_id=certificate.registration_id).first()


def signature_present(signatory: CertificateSignatory | None) -> bool:
    """The signatory's signature image is stored and is a PNG or JPG (the file is read: it can disappear from the
    disk)."""
    field_file = signatory.signature_image_path if signatory is not None else None
    if not field_file or not field_file.name:
        return False
    try:
        with field_file.storage.open(field_file.name, "rb") as handle:
            head = handle.read(len(document_services.PNG_SIGNATURE))
    except (FileNotFoundError, OSError, ValueError):
        return False
    return head.startswith((document_services.PNG_SIGNATURE, document_services.JPEG_SIGNATURE))


def add_signature_reason(signatory: CertificateSignatory) -> str:
    """ "Add the signature of Dr. Maria Santos (University President) first." (approval is refused, ASSUMPTIONS 363)."""
    return f"Add the signature of {signatory_label(signatory)} first."


def missing_signature_reason(signatories) -> str | None:
    """None when every given signatory has a signature image; otherwise the reason approval is refused (the first
    one missing, in the order given: left before right). Certificates that are not Issued always use the activity's
    current signatories (every change refreshes them)."""
    seen: dict[int, CertificateSignatory] = {}
    for signatory in signatories:
        seen.setdefault(signatory.pk, signatory)
    for signatory in seen.values():
        if not signature_present(signatory):
            return add_signature_reason(signatory)
    return None


def _certificate_signatories(certificates) -> list[CertificateSignatory]:
    return [person for c in certificates for person in (c.left_signatory, c.right_signatory)]


# --- Ready for approval (computed, never stored; ASSUMPTIONS 362) ---------------------------------

WAITING_FOR_EVALUATION = "Waiting for evaluation"
WAITING_FOR_SIGNATURES = "Waiting for signatures"
# Until the TBI answers (TBI_QUESTIONS): no certificate is sent, and it is not a staff task.
EVALUATION_CLOSED = "Evaluation closed without an answer"


def ready_for_approval_q(prefix: str = "") -> Q:
    """The database version of "ready for approval" (counts: the dashboard's S5, Needs attention): waiting for approval,
    both signatories have a signature image stored, and the recipient is a resource person or has evaluated.
    `prefix` e.g. "certificates__" when filtering another table. readiness() checks the same and also reads the
    image files (a file missing from the disk counts as no signature: approval refuses it)."""
    def f(name: str) -> str:
        return prefix + name

    return (
        Q(**{f("status"): Status.GENERATED})
        & Q(**{f("left_signatory__signature_image_path__gt"): ""})  # NULL and "" are both excluded
        & Q(**{f("right_signatory__signature_image_path__gt"): ""})
        & (Q(**{f("recipient_type"): RecipientType.RESOURCE_PERSON})
           | Q(**{f("registration__evaluation_submission__isnull"): False}))
    )


def readiness(certificate: Certificate, *, evaluated: bool | None = None, signatures: dict | None = None,
              now: datetime | None = None) -> str | None:
    """None when the certificate is ready for approval (or is not waiting for approval at all); otherwise the label
    shown instead of the Approve button:
    - "Evaluation closed without an answer": a participant who did not evaluate before evaluation_deadline (never
      ready: no certificate is sent until the TBI decides otherwise; not a staff task);
    - "Waiting for signatures": the left or the right signatory's image is missing (ASSUMPTIONS 363: the activity's
      two signatories are chosen and both have an image);
    - "Waiting for evaluation": the participant has not submitted the evaluation (the printed name and email come
      from it).
    A resource person's certificate needs only the signatures. `evaluated` / `signatures` (signatory pk -> present)
    let readiness_map() check many certificates with fixed queries."""
    if certificate.status != Status.GENERATED:
        return None
    signatures = {} if signatures is None else signatures

    def present(signatory: CertificateSignatory) -> bool:
        if signatory.pk not in signatures:
            signatures[signatory.pk] = signature_present(signatory)
        return signatures[signatory.pk]

    participant = certificate.recipient_type == RecipientType.REGISTRATION
    if participant:
        if evaluated is None:
            evaluated = get_submitted_evaluation(certificate) is not None
        if not evaluated and (now or timezone.now()) > certificate.activity.evaluation_deadline:
            return EVALUATION_CLOSED
    if not (present(certificate.left_signatory) and present(certificate.right_signatory)):
        return WAITING_FOR_SIGNATURES
    if participant and not evaluated:
        return WAITING_FOR_EVALUATION
    return None


def readiness_map(certificates: list[Certificate], now: datetime | None = None) -> dict[int, str | None]:
    """readiness() of many certificates (certificate pk -> label or None) with one query for the evaluations; each
    signature file is read once. The certificates should come with activity, left_signatory and right_signatory."""
    registration_ids = [c.registration_id for c in certificates if c.registration_id and c.status == Status.GENERATED]
    evaluated = set(EvaluationSubmission.objects.filter(registration_id__in=registration_ids)
                    .values_list("registration_id", flat=True)) if registration_ids else set()
    signatures: dict[int, bool] = {}
    now = now or timezone.now()
    return {
        c.pk: readiness(c, evaluated=c.registration_id in evaluated, signatures=signatures, now=now)
        for c in certificates
    }


def _waiting(activity: Activity):
    return (Certificate.objects.filter(activity=activity, status=Status.GENERATED)
            .select_related("activity", "left_signatory", "right_signatory").order_by("certificate_number"))


# --- Approval per activity (ASSUMPTIONS 369) -----------------------------------------------------------------------

APPROVAL_HISTORY_LABEL = "Certificate approval"
NEEDS_APPROVAL = "Needs approval"
ALREADY_APPROVED = "The certificates of this activity are already approved."
NOTHING_TO_APPROVE = "There are no certificates to approve yet: they are created when the activity is Conducted."
ALL_ISSUED = "Every certificate of this activity is already issued."


def is_approved(activity: Activity) -> bool:
    """True when the activity's certificates are approved (ACTIVITY.certificates_approved_at is set)."""
    return activity.certificates_approved_at is not None


def approved_text(activity: Activity) -> str:
    """ "Approved by Ana Cruz on November 14, 2026, 3:05 PM" (Manila), or NEEDS_APPROVAL."""
    if not is_approved(activity):
        return NEEDS_APPROVAL
    at = timezone.localtime(activity.certificates_approved_at)
    who = activity.certificates_approved_by.name if activity.certificates_approved_by_id else "a member"
    return f"Approved by {who} on {pdf.long_date(at)}, {at.hour % 12 or 12}:{at:%M %p}"


NOT_APPROVED_NOTHING_WAITING = "Not approved — new certificates will wait for approval"
NO_CERTIFICATES_YET = "Certificates are created automatically when the activity is Conducted."
ALL_ISSUED_TEXT = "All certificates issued ({count})"
ALL_ISSUED_NOTE = ("Nothing to approve. A certificate added later (attendance marked after Conducted) will show here for "
                   "approval.")


@dataclass(frozen=True)
class ApprovalState:
    """What the approval means for the user (ASSUMPTIONS 373). `kind`: APPROVED_KIND ("Approved by … on …"),
    NEEDS_APPROVAL_KIND (not approved and certificates waiting: a task, orange), NO_CERTIFICATES_KIND (none yet),
    ALL_ISSUED_KIND (not approved, every certificate Issued: nothing to approve) or NOT_APPROVED_KIND (not approved,
    nothing waiting, some not Issued: old data). Only NEEDS_APPROVAL_KIND is ever counted. `note`: the short grey line
    under the text. `offers_approval`: False = no approve button at all (not even disabled), no reason line."""

    kind: str
    text: str
    note: str = ""
    offers_approval: bool = True


APPROVED_KIND, NEEDS_APPROVAL_KIND, NOT_APPROVED_KIND = "approved", "needs_approval", "not_approved"
NO_CERTIFICATES_KIND, ALL_ISSUED_KIND = "no_certificates", "all_issued"


def approval_state(activity: Activity, counts: dict[str, int]) -> ApprovalState:
    """The one rule for showing an activity's certificate approval (the Certificates page header and the tab's card).
    `counts` = {status: n} of ALL the activity's certificates, never only the ones a filter shows (approval_counts).
    In order: approved; certificates waiting (agrees with needs_approval_q: NEEDS_APPROVAL_KIND exactly when that rule
    counts the activity); no certificates yet; every certificate Issued; otherwise the grey note."""
    if is_approved(activity):
        return ApprovalState(APPROVED_KIND, approved_text(activity))
    if counts.get(Status.GENERATED, 0):
        return ApprovalState(NEEDS_APPROVAL_KIND, NEEDS_APPROVAL)
    total = sum(counts.values())
    if not total:
        return ApprovalState(NO_CERTIFICATES_KIND, NO_CERTIFICATES_YET, offers_approval=False)
    if counts.get(Status.ISSUED, 0) == total:
        return ApprovalState(ALL_ISSUED_KIND, ALL_ISSUED_TEXT.format(count=total), ALL_ISSUED_NOTE, offers_approval=False)
    return ApprovalState(NOT_APPROVED_KIND, NOT_APPROVED_NOTHING_WAITING)


def approval_counts(activity_ids: list[int]) -> dict[int, dict[str, int]]:
    """{activity_id: {status: n}} of every certificate of each activity (0 for every status when none). One query."""
    counts = {pk: dict.fromkeys(Status.values, 0) for pk in activity_ids}
    if activity_ids:
        rows = (Certificate.objects.filter(activity_id__in=activity_ids).order_by()
                .values("activity_id", "status").annotate(n=Count("pk")))
        for row in rows:
            counts[row["activity_id"]][row["status"]] = row["n"]
    return counts


def needs_approval_q(prefix: str = "") -> Q:
    """Certificates that make their activity "need certificate approval": waiting for approval and the activity is not
    approved. `prefix` e.g. "certificates__". The dashboard (S5), Needs attention and the counts all use it."""
    return Q(**{f"{prefix}status": Status.GENERATED}) & Q(**{f"{prefix}activity__certificates_approved_at__isnull": True})


def approval_closed_reason(activity: Activity, now: datetime | None = None) -> str | None:
    """Why "Approve certificates for this activity" cannot be used now, or None. Read only: the Certificates tab shows
    it on the disabled button (approvers only) and approve_activity_certificates refuses with it. Already approved; no
    certificates yet; every certificate Issued; the design incomplete (no active design, a slot empty); a signature
    image missing ("Add the signature of ... first.": certificates are emailed without another click)."""
    if is_approved(activity):
        return ALREADY_APPROVED
    statuses = set(Certificate.objects.filter(activity=activity).values_list("status", flat=True))
    if not statuses:
        return NOTHING_TO_APPROVE
    if statuses == {Status.ISSUED}:
        return ALL_ISSUED
    try:
        fields = design_fields(activity)
    except CertificateError as error:
        return str(error)
    return missing_signature_reason([fields["left_signatory"], fields["right_signatory"]])


@transaction.atomic
def approve_activity_certificates(member: TBIMember, activity: Activity, now: datetime | None = None) -> list[Certificate]:
    """"Approve certificates for this activity" (can_approve_certificates, after checking the preview): the approval is
    stored on the activity (who and when; one History row) and every certificate that is ready now is approved
    (approve_ready_certificates); the others are approved by the System as soon as they are ready (a later
    evaluation, a certificate created later). Refused with approval_closed_reason. Returns the certificates approved
    now."""
    from activities.services import write_certificate_approval_history

    require_permission(member, Permission.APPROVE_CERTIFICATES)
    now = now or timezone.now()
    locked = Activity.objects.select_for_update(of=("self",)).select_related("status").get(pk=activity.pk)
    if reason := approval_closed_reason(locked, now):
        raise CertificateError(reason)
    Activity.objects.filter(pk=locked.pk).update(certificates_approved_by=member, certificates_approved_at=now)
    locked.certificates_approved_by, locked.certificates_approved_at = member, now
    write_certificate_approval_history(locked, member, NEEDS_APPROVAL, f"Approved by {member.name}")
    approved = approve_ready_certificates(locked, now=now)
    audit_logger.info("Activity certificates approved: activity_id=%s certificates_approved_now=%s by member_id=%s",
                      locked.pk, len(approved), member.pk)
    return approved


def approve_ready_certificates(activity: Activity, *, certificate_ids: list[int] | None = None,
                               now: datetime | None = None) -> list[Certificate]:
    """By the System, for an APPROVED activity only: every certificate waiting for approval that is ready (readiness)
    becomes Approved in the approver's name (approved_by = the activity's approver, approved_at = now); after the
    commit its PDF is rendered (a participant's is then issued and emailed; a resource person's waits for the
    hand-over). `certificate_ids`: only these. Nothing happens for an activity that is not approved. Safe to run any
    number of times (the activity and the certificates are locked). Returns the certificates approved."""
    with transaction.atomic():
        locked = (Activity.objects.select_for_update(of=("self",)).select_related("certificates_approved_by")
                  .get(pk=activity.pk))
        if not is_approved(locked):
            return []
        waiting = _waiting(locked).select_for_update(of=("self",))
        if certificate_ids is not None:
            waiting = waiting.filter(pk__in=certificate_ids)
        waiting = list(waiting)
        labels = readiness_map(waiting, now)
        ready = [c for c in waiting if labels[c.pk] is None]
        for certificate in ready:
            set_certificate_status(certificate, Status.APPROVED, member=locked.certificates_approved_by, now=now)
            if _not_ready_reason(certificate) is None:
                # robust=True: a problem queueing never undoes the approval (the 5-minute job catches it up).
                transaction.on_commit(lambda certificate_id=certificate.pk: queue_prepare(certificate_id), robust=True)
    if ready:
        logger.info("Certificates %s approved for approved activity %s.", ", ".join(str(c.pk) for c in ready), locked.pk)
    return ready


def approve_pending_certificates(now: datetime | None = None) -> int:
    """The 5-minute job's catch-up: approve the ready certificates of every approved activity (an evaluation whose
    after-commit call was lost, a signature image added back). Returns how many were approved."""
    activities = (Activity.objects.filter(certificates_approved_at__isnull=False)
                  .filter(Exists(Certificate.objects.filter(ready_for_approval_q(), activity=OuterRef("pk"))))
                  .order_by("pk"))
    return sum(len(approve_ready_certificates(activity, now=now)) for activity in activities)


def cancel_certificate_approval(activity: Activity, member: TBIMember | None, now: datetime | None, reason: str) -> bool:
    """Something printed on the activity's certificates changed after the approval (a signatory, a title, a new version
    of a person, "Apply the current design", the certificate text): the approval is cancelled (the activity "Needs
    approval" again; one History row by the member who made the change) and its Approved certificates go back to
    waiting (_refresh_certificates: the PDF is removed). Issued certificates never change. Must run inside the
    caller's transaction. Returns True when an approval was cancelled."""
    from activities.services import write_certificate_approval_history

    locked = (Activity.objects.select_for_update(of=("self",)).select_related("certificates_approved_by")
              .get(pk=activity.pk))
    if not is_approved(locked):
        return False
    old = f"Approved by {locked.certificates_approved_by.name if locked.certificates_approved_by_id else 'a member'}"
    Activity.objects.filter(pk=locked.pk).update(certificates_approved_by=None, certificates_approved_at=None)
    activity.certificates_approved_by, activity.certificates_approved_at = None, None
    write_certificate_approval_history(locked, member, old, f"{NEEDS_APPROVAL} (cancelled: {reason})")
    approved = list(Certificate.objects.select_for_update(of=("self",)).filter(activity=locked, status=Status.APPROVED)
                    .order_by("pk"))
    _refresh_certificates(approved, member, now)
    audit_logger.info("Activity certificate approval cancelled: activity_id=%s certificates_back=%s by member_id=%s",
                      locked.pk, len(approved), member.pk if member else None)
    return True


def set_certificate_status(
    certificate: Certificate, new_status: str, *, member: TBIMember | None = None, now: datetime | None = None
) -> Certificate:
    """Move a certificate to `new_status` after checking the rules, then save it.

    - Only the moves in ALLOWED_MOVES are possible.
    - Approved needs both signature images (missing_signature_reason); it sets approved_by and approved_at.
    - Back to Generated (the design applied again, the activity's signatory changed or a new version of a signatory,
      ASSUMPTIONS 363): clears approved_by and approved_at.
    - Issued needs, for participants, a submitted evaluation. Issuing sets issued_at and links
      the evaluation to the certificate.
    """
    now = now or timezone.now()
    if new_status not in Status.values:
        raise CertificateStatusError(f"Unknown certificate status: {new_status!r}.")
    if new_status not in ALLOWED_MOVES[certificate.status]:
        raise CertificateStatusError(
            f"Cannot move a certificate from {Status(certificate.status).label} to {Status(new_status).label}."
        )

    if new_status == Status.APPROVED:
        if reason := missing_signature_reason([certificate.left_signatory, certificate.right_signatory]):
            raise CertificateStatusError(reason)
        certificate.approved_by = member
        certificate.approved_at = now

    elif new_status == Status.GENERATED:  # regenerated: must be approved again
        certificate.approved_by = None
        certificate.approved_at = None

    elif new_status == Status.ISSUED:
        if certificate.recipient_type == RecipientType.REGISTRATION:
            evaluation = get_submitted_evaluation(certificate)
            if evaluation is None:
                raise CertificateStatusError("Cannot issue: the participant has not submitted the evaluation.")
            certificate.evaluation_submission = evaluation
        certificate.issued_at = now

    certificate.status = new_status
    certificate.save(update_fields=["status", "approved_by", "approved_at", "evaluation_submission", "issued_at"])
    return certificate


# --- Refreshing certificates that are not Issued ---------------------------------------------------


def _refresh_certificates(certificates: list[Certificate], member: TBIMember, now: datetime | None, **changes) -> None:
    """Apply design changes (e.g. a new signatory) to certificates that are not Issued: the rendered PDF is removed
    after the commit (rendered again on approval / evaluation) and Approved ones go back to "Waiting for approval".
    Must run inside a transaction with the rows locked."""
    storage = Certificate._meta.get_field("file_path").storage
    for certificate in certificates:
        if certificate.status == Status.ISSUED:
            raise CertificateError("Issued certificates are final.")
        if certificate.file_path:
            transaction.on_commit(lambda name=certificate.file_path.name: storage.delete(name), robust=True)
        for name, value in changes.items():
            setattr(certificate, name, value)
        certificate.file_path = None
        certificate.render_failed_at = None
        certificate.render_error = None
        certificate.save(update_fields=[*changes, "file_path", "render_failed_at", "render_error"])
        if certificate.status == Status.APPROVED:
            set_certificate_status(certificate, Status.GENERATED, member=member, now=now)


def upload_signature(member: TBIMember, activity: Activity, slot: str, file: UploadedFile | None,
                     now: datetime | None = None, keep_background: bool = False) -> dict:
    """The activity's Certificates tab: add or replace the signature image of the person on `slot`
    (replace_signature: a replaced image is a new version for every activity that uses the person; a white
    background is made transparent unless keep_background). can_manage_signatories (ASSUMPTIONS 418)."""
    require_permission(member, Permission.MANAGE_SIGNATORIES)
    if slot not in SLOTS:
        raise ValidationError({"slot": ["Choose left or right."]})
    signatory = activity_signatories(activity)[slot]
    if signatory is None:
        raise CertificateError(missing_slots_reason(activity_signatories(activity)))
    with transaction.atomic():
        Activity.objects.select_for_update(of=("self",)).get(pk=activity.pk)
        return replace_signature(member, signatory, file, now=now, keep_background=keep_background)


# --- Attendance undone after the certificate was created (ASSUMPTIONS 362) ------------------------


def undo_attendance_refused_reason(registration) -> str | None:
    """Why attendance cannot be undone because of the person's certificate, or None. A certificate still waiting for
    approval (not approved, not issued, no evidence) is deleted with the undo; any other blocks it."""
    certificate = registration.certificates.first()
    if certificate is None:
        return None
    if certificate.status != Status.GENERATED:
        return "the certificate was already approved."
    if certificate.documents.filter(related_certificate__isnull=False).exists():
        return "the certificate has evidence documents."
    return None


def delete_certificate_for_undone_attendance(member: TBIMember, registration) -> str | None:
    """Delete the person's certificate when their attendance is undone (registrations.services.set_attendance, inside
    its transaction, after undo_attendance_refused_reason allowed it). Its number is not reused (a gap). One History
    row by the member. Returns the deleted certificate's number, or None when there was none."""
    from activities.services import write_certificate_history

    certificate = Certificate.objects.select_for_update(of=("self",)).filter(registration=registration).first()
    if certificate is None:
        return None
    if reason := undo_attendance_refused_reason(registration):
        raise CertificateError(reason)
    number = certificate.certificate_number
    if certificate.file_path:
        storage = Certificate._meta.get_field("file_path").storage
        transaction.on_commit(lambda name=certificate.file_path.name: storage.delete(name), robust=True)
    certificate.delete()
    write_certificate_history(registration.activity, member, f"{number} ({registration.name})",
                              "Deleted: attendance undone")
    return number


# --- Preview helpers ----------------------------------------------------------------------------------


def preview_version(certificate: Certificate, evaluated: bool) -> str:
    """A short value that changes whenever the preview would look different (status, design, signatories and their
    images, the final file, a reissue, the evaluation). Added to the preview and download URLs (?v=...) so a browser
    never shows an old copy."""
    raw = "|".join(str(value) for value in (
        certificate.status, certificate.certificate_template_id, certificate.left_signatory_id,
        certificate.left_signatory.signature_image_path.name or "", certificate.right_signatory_id,
        certificate.right_signatory.signature_image_path.name or "", certificate.left_signatory_title,
        certificate.right_signatory_title,
        certificate.file_path.name if certificate.file_path else "", certificate.reissued_at,
        certificate.evaluation_submission_id, evaluated,
    ))
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def shows_final_copy(certificate: Certificate) -> bool:
    """An Issued certificate's preview is its stored final PDF: exactly the copy that was sent or handed over. It
    changes only when a member reissues it (ASSUMPTIONS 364); while the updated copy is being prepared (no file) the
    preview is drawn now."""
    return certificate.status == Status.ISSUED and bool(certificate.file_path)


def updated_copy_pending(certificate: Certificate) -> bool:
    """Reissued and its new PDF not rendered yet (the background worker does it, then emails a participant's)."""
    return certificate.status == Status.ISSUED and certificate.reissued_at is not None and not certificate.file_path


def awaiting_hand_over(certificate: Certificate) -> bool:
    """A hard copy to hand over: Approved, or Issued and reissued after it was last handed over ("Not handed over
    (updated copy to print)", ASSUMPTIONS 364)."""
    if certificate.delivery_method != DeliveryMethod.HARD_COPY:
        return False
    if certificate.status == Status.APPROVED:
        return True
    return (certificate.status == Status.ISSUED and certificate.reissued_at is not None
            and (certificate.issued_at is None or certificate.reissued_at > certificate.issued_at))


def reprint_q() -> Q:
    """Issued hard copies reissued after their last hand-over (awaiting_hand_over, database version)."""
    return Q(delivery_method=DeliveryMethod.HARD_COPY, status=Status.ISSUED, reissued_at__isnull=False,
             reissued_at__gt=F("issued_at"))


# --- Issued with an old signature, reissue (ASSUMPTIONS 364) -----------------------------------------


def outdated_q() -> Q:
    """Issued certificates whose left or right signatory, or the title printed for them, is not the activity's
    current one on that side (computed: a replaced image or an edited name is a new row, so comparing the rows and the
    stored titles is enough; ASSUMPTIONS 364, 365). "Issued with an old signature"."""
    current = ActivitySignatory.objects.filter(activity=OuterRef("activity_id")).annotate(
        printed=Coalesce(NullIf("position_on_certificate", Value("")), "signatory__position"))
    return Q(status=Status.ISSUED) & (
        ~Q(Exists(current.filter(slot=Slot.LEFT, signatory=OuterRef("left_signatory_id"),
                                 printed=OuterRef("left_signatory_title"))))
        | ~Q(Exists(current.filter(slot=Slot.RIGHT, signatory=OuterRef("right_signatory_id"),
                                   printed=OuterRef("right_signatory_title"))))
    )


def outdated_certificates(activity: Activity) -> QuerySet:
    return Certificate.objects.filter(activity=activity).filter(outdated_q())


def outdated_ids(activity: Activity) -> set[int]:
    return set(outdated_certificates(activity).values_list("pk", flat=True))


def outdated_counts(activity: Activity) -> dict[str, int]:
    """{"total", "by_email", "by_hand"}: the confirm dialog of "Reissue all outdated certificates (N)"."""
    rows = dict(outdated_certificates(activity).order_by().values("delivery_method").annotate(n=Count("pk"))
                .values_list("delivery_method", "n"))
    by_email, by_hand = rows.get(DeliveryMethod.EMAIL, 0), rows.get(DeliveryMethod.HARD_COPY, 0)
    return {"total": by_email + by_hand, "by_email": by_email, "by_hand": by_hand}


def outdated_notice(count: int, activities: int = 1) -> str:
    """The message after a signatory change (ASSUMPTIONS 364); "" when nothing is outdated."""
    if not count:
        return ""
    where = f" (in {_plural(activities, 'activity', 'activities')})" if activities > 1 else ""
    return (f"{_plural(count, 'issued certificate still has', 'issued certificates still have')} the old signature"
            f"{where}. Reissue them from the Certificates tab.")


def reissue_closed_reason(activity: Activity) -> str | None:
    """Why outdated certificates of `activity` cannot be reissued now, or None: an active design and both current
    signatories with an image are needed."""
    if active_template() is None:
        return NO_TEMPLATE
    signatories = saved_activity_signatories(activity)
    if reason := missing_slots_reason(signatories):
        return reason
    return missing_signature_reason([signatories[slot] for slot in SLOTS])


def reissue_text(numbers: list[str]) -> str:
    """The History row: "Reissued with the current signatures: TBI-2026-00001, TBI-2026-00002"."""
    shown = ", ".join(numbers[:20]) + (f" and {len(numbers) - 20} more" if len(numbers) > 20 else "")
    return f"Reissued with the current signatures: {shown}"


@transaction.atomic
def reissue_certificates(member: TBIMember, *, certificate_ids: list[int] | None = None, activity: Activity | None = None,
                         now: datetime | None = None) -> int:
    """Reissue Issued certificates that have an old signature (can_approve_certificates; ASSUMPTIONS 364): the
    activity's current signatories and the active design are put on them, the number and the status Issued stay,
    reissued_at is set and the PDF is rendered again in the background (prepare_certificate). Then a participant's is
    emailed again ("Updated copy"; sent_at / send_failed_at cleared until it is sent) and a resource person's waits to
    be handed over again. The old PDF is replaced when the new one is written. All or nothing; one History row."""
    from activities.services import write_certificate_history

    require_permission(member, Permission.APPROVE_CERTIFICATES)
    now = now or timezone.now()
    if activity is not None:
        ids = sorted(outdated_ids(activity))
        if not ids:
            raise CertificateError("No issued certificates of this activity have an old signature.")
        certificates = _select_for_action(ids, None, Status.ISSUED)
    else:
        certificates = _select_for_action(certificate_ids, None, Status.ISSUED)
    activity_ids = {c.activity_id for c in certificates}
    if len(activity_ids) != 1:
        raise CertificateError("Reissue the certificates of one activity at a time.")
    target = Activity.objects.select_for_update(of=("self",)).get(pk=activity_ids.pop())
    if reason := reissue_closed_reason(target):
        raise CertificateError(reason)
    outdated = outdated_ids(target)
    design = design_fields(target)
    storage = Certificate._meta.get_field("file_path").storage

    def reissue(certificate: Certificate) -> None:
        if certificate.status != Status.ISSUED:
            raise CertificateError("only Issued certificates are reissued.")
        if certificate.pk not in outdated:
            raise CertificateError("it already has the current signatures.")
        old_file = certificate.file_path.name if certificate.file_path else ""
        if old_file and old_file != final_pdf_name(certificate):  # the new PDF replaces final_pdf_name() itself
            transaction.on_commit(lambda name=old_file: storage.delete(name), robust=True)
        for name, value in design.items():
            setattr(certificate, name, value)
        certificate.certificate_text = wording.current_text_for(target.pk, certificate.text_type)  # ASSUMPTIONS 370
        certificate.file_path = None
        certificate.sent_at = None
        certificate.send_failed_at = None
        certificate.render_failed_at = None
        certificate.render_error = None
        certificate.reissued_at = now
        certificate.save(update_fields=[*design, "certificate_text", "file_path", "sent_at", "send_failed_at",
                                        "render_failed_at", "render_error", "reissued_at"])

    _apply_all(certificates, reissue)
    write_certificate_history(target, member, "", reissue_text([c.certificate_number for c in certificates]))
    audit_logger.info("Certificates reissued: activity_id=%s certificate_ids=%s by member_id=%s", target.pk,
                      ",".join(str(c.pk) for c in certificates), member.pk)
    for certificate in certificates:
        transaction.on_commit(lambda certificate_id=certificate.pk: queue_prepare(certificate_id), robust=True)
    return len(certificates)


def reissue_certificate(member: TBIMember, certificate: Certificate, now: datetime | None = None) -> Certificate:
    """The preview's "Reissue with current signatures": one certificate."""
    reissue_certificates(member, certificate_ids=[certificate.pk], now=now)
    certificate.refresh_from_db()
    return certificate


# --- Actions: one certificate, a list, or all of an activity --------------------------------------


def _select_for_action(certificate_ids: list[int] | None, activity: Activity | None, statuses, extra: Q | None = None,
                       empty: str = "") -> list[Certificate]:
    """Lock the certificates to act on: the given IDs, or every certificate of `activity` in one of `statuses`
    (and matching `extra`). Must be called inside a transaction."""
    if (certificate_ids is None) == (activity is None):
        raise CertificateError("Give either certificate_ids or activity_id.")
    statuses = [statuses] if isinstance(statuses, str) else list(statuses)
    queryset = Certificate.objects.select_for_update(of=("self",)).select_related("left_signatory", "right_signatory").order_by("pk")
    if activity is not None:
        queryset = queryset.filter(activity=activity, status__in=statuses)
        if extra is not None:
            queryset = queryset.filter(extra)
        certificates = list(queryset)
        if not certificates:
            labels = " or ".join(Status(s).label for s in statuses)
            raise CertificateError(empty or f"This activity has no certificates to act on in {labels}.")
        return certificates
    ids = {int(i) for i in certificate_ids}
    certificates = list(queryset.filter(pk__in=ids))
    if not ids or len(certificates) != len(ids):
        raise CertificateError("Some certificates were not found.")
    return certificates


def _apply_all(certificates: list[Certificate], action) -> None:
    """Run `action` on every certificate; if any is refused, raise one error listing all of them.
    The caller's transaction then rolls back, so it is all or nothing."""
    problems = []
    for certificate in certificates:
        try:
            action(certificate)
        except CertificateError as error:
            problems.append(f"{certificate.certificate_number}: {error}")
    if problems:
        raise CertificateError("Nothing was changed. " + " ".join(problems), problems=problems)


@transaction.atomic
def regenerate_certificates(member: TBIMember, *, certificate_ids: list[int] | None = None, activity: Activity | None = None, now: datetime | None = None) -> int:
    """Apply the active design and the activity's current signatories to certificates that are not Issued, remove
    their rendered PDF and send them back to "Waiting for approval" (they are approved again). The number stays.
    Issued certificates are refused here (they are reissued instead: reissue_certificates)."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    certificates = _select_for_action(certificate_ids, activity, [Status.GENERATED, Status.APPROVED],
                                      empty="This activity has no certificates to regenerate (Issued certificates are final).")
    designs = {}
    # What is printed changes: the approval of each activity is cancelled first (ASSUMPTIONS 369); its Approved
    # certificates are then already back to waiting, so the rows are read again.
    for activity_id in sorted({c.activity_id for c in certificates}):
        cancel_certificate_approval(Activity(pk=activity_id), member, now, "current design applied")
    for certificate in certificates:
        certificate.refresh_from_db()

    def regenerate(certificate: Certificate) -> None:
        if certificate.status == Status.ISSUED:
            raise CertificateError("Issued certificates are final and cannot be regenerated.")
        if certificate.activity_id not in designs:
            designs[certificate.activity_id] = design_fields(certificate.activity)
        text = wording.current_text_for(certificate.activity_id, certificate.text_type)
        _refresh_certificates([certificate], member, now, **designs[certificate.activity_id], certificate_text=text)

    _apply_all(certificates, regenerate)
    logger.info("Member %s regenerated certificates %s.", member.pk, ", ".join(str(c.pk) for c in certificates))
    return len(certificates)


@transaction.atomic
def mark_handed_over(member: TBIMember, *, certificate_ids: list[int] | None = None, activity: Activity | None = None, now: datetime | None = None) -> int:
    """Approved -> Issued for hard-copy certificates (resource persons), when the printed
    certificate is handed over. Needs the rendered PDF (staff print it). Never emailed.
    A reissued hard copy (Issued, "Not handed over (updated copy to print)", ASSUMPTIONS 364) stays Issued and gets a
    new issued_at (the hand-over of the updated copy)."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    now = now or timezone.now()
    certificates = _select_for_action(certificate_ids, activity, [Status.APPROVED, Status.ISSUED],
                                      Q(status=Status.APPROVED, delivery_method=DeliveryMethod.HARD_COPY) | reprint_q())

    def hand_over(certificate: Certificate) -> None:
        if certificate.delivery_method != DeliveryMethod.HARD_COPY:
            raise CertificateError("only hard-copy certificates are handed over; participant certificates are issued automatically.")
        if not awaiting_hand_over(certificate):
            raise CertificateError("it was already handed over.")
        if not certificate.file_path:
            raise CertificateError("the PDF is not ready yet. Try again in a moment.")
        if certificate.status == Status.ISSUED:
            certificate.issued_at = now
            certificate.save(update_fields=["issued_at"])
        else:
            set_certificate_status(certificate, Status.ISSUED, member=member, now=now)

    _apply_all(certificates, hand_over)
    return len(certificates)


@transaction.atomic
def resend_certificates(member: TBIMember, *, certificate_ids: list[int] | None = None, activity: Activity | None = None) -> int:
    """Queue the email again for Issued participant certificates that were not sent (usually "Send failed").
    send_certificate still sends each one at most once."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    certificates = _select_for_action(
        certificate_ids, activity, Status.ISSUED, Q(delivery_method=DeliveryMethod.EMAIL, sent_at__isnull=True),
        empty="This activity has no certificate emails waiting to be sent.",
    )

    def check(certificate: Certificate) -> None:
        if certificate.delivery_method != DeliveryMethod.EMAIL:
            raise CertificateError("hard-copy certificates are never emailed.")
        if certificate.status != Status.ISSUED:
            raise CertificateError("only Issued certificates are emailed (approved and evaluated).")
        if certificate.sent_at is not None:
            raise CertificateError("it was already sent.")
        if not certificate.file_path:
            raise CertificateError("the updated copy is still being prepared.")

    _apply_all(certificates, check)
    for certificate in certificates:
        transaction.on_commit(lambda certificate_id=certificate.pk: queue_send(certificate_id), robust=True)
    return len(certificates)


# --- Final PDF and issuing --------------------------------------------------------------------


def recipient_name(certificate: Certificate) -> str | None:
    """The name printed: the participant's certificate_name (from the evaluation) or the resource
    person's name. None while a participant has not evaluated yet."""
    if certificate.recipient_type == RecipientType.RESOURCE_PERSON:
        return certificate.resource_person.name
    evaluation = get_submitted_evaluation(certificate)
    return evaluation.certificate_name if evaluation else None


def _not_ready_reason(certificate: Certificate) -> str | None:
    """None when the final PDF can be rendered now (Approved and ready, or reissued: updated_copy_pending); otherwise
    why not."""
    if updated_copy_pending(certificate):
        return None
    if certificate.status != Status.APPROVED:
        return "not Approved"
    if certificate.file_path:
        return "PDF already rendered"
    if certificate.recipient_type == RecipientType.REGISTRATION and get_submitted_evaluation(certificate) is None:
        return "waiting for the evaluation"
    return None


def _design(certificate: Certificate) -> tuple:
    return (certificate.certificate_template_id, certificate.left_signatory_id, certificate.right_signatory_id,
            certificate.left_signatory_title, certificate.right_signatory_title, certificate.certificate_text_id,
            certificate.reissued_at)


def final_pdf_name(certificate: Certificate) -> str:
    """certificates/<year from the number>/<number>.pdf"""
    year = certificate.certificate_number.split("-")[1]
    return f"certificates/{year}/{certificate.certificate_number}.pdf"


def _write_file_atomically(name: str, content: bytes) -> None:
    """The final file is either complete or absent, never half-written (config.files)."""
    write_file_atomically(Certificate._meta.get_field("file_path").storage, name, content)


CERTIFICATE_GONE = "certificate no longer exists"


def prepare_certificate(certificate_id: int, now: datetime | None = None) -> str:
    """Render the final PDF of an Approved certificate that is ready; issue a participant's. A reissued certificate
    (updated_copy_pending, ASSUMPTIONS 364) gets its new PDF (replacing the old file at the same name) and a
    participant's is queued to be emailed again.

    Returns "rendered", "issued", "reissued" or the reason nothing was done. Raises if rendering or saving fails
    (the task logs it); then no file is left and the certificate stays Approved for a retry.
    """
    certificate = Certificate.objects.select_related(
        "activity", "certificate_template", "left_signatory", "right_signatory", "resource_person"
    ).filter(pk=certificate_id).first()
    if certificate is None:  # deleted since it was queued (its activity was deleted, ASSUMPTIONS 389)
        return CERTIFICATE_GONE
    if reason := _not_ready_reason(certificate):
        return reason
    content = pdf.render_certificate_pdf(certificate, name=recipient_name(certificate))  # slow part, no lock held

    name = final_pdf_name(certificate)
    written = False
    try:
        with transaction.atomic():
            locked = Certificate.objects.select_for_update().get(pk=certificate_id)
            if reason := _not_ready_reason(locked):  # another task finished first
                return reason
            if _design(locked) != _design(certificate):  # regenerated while rendering: the next task renders it
                return "design changed"
            _write_file_atomically(name, content)
            written = True
            locked.file_path.name = name
            locked.render_failed_at = None
            locked.render_error = None
            locked.save(update_fields=["file_path", "render_failed_at", "render_error"])
            if locked.status == Status.ISSUED:  # reissued: the updated copy
                if locked.delivery_method == DeliveryMethod.EMAIL:
                    transaction.on_commit(lambda: queue_send(certificate_id), robust=True)
                result = "reissued"
            elif locked.recipient_type == RecipientType.REGISTRATION:
                set_certificate_status(locked, Status.ISSUED, now=now)
                transaction.on_commit(lambda: queue_send(certificate_id), robust=True)
                result = "issued"
            else:
                result = "rendered"
    except BaseException:
        if written:
            Certificate._meta.get_field("file_path").storage.delete(name)
        raise
    return result


# --- Why an Approved certificate has no PDF yet (ASSUMPTIONS 368) --------------------------------------------------

PDF_PRE_RULE_WAITING = "pre_rule_waiting"  # approved before the evaluation rule, evaluation still open
PDF_PRE_RULE_CLOSED = "pre_rule_closed"  # approved before the evaluation rule, evaluation closed without an answer
PDF_FAILED = "failed"  # the last render failed (render_error): Retry
PDF_WORKER = "worker"  # qcluster is not running
PDF_PREPARING = "preparing"  # queued / being rendered
STALLED_MINUTES = 10  # the 5-minute job queues a render that has not happened after this long


def approved_before_evaluation_rule(certificate: Certificate, evaluated: bool) -> bool:
    """An Approved participant certificate without a submitted evaluation. Since item 362 approval needs the
    evaluation (and every refresh sends an Approved certificate back to waiting), so only certificates approved
    BEFORE that rule can be in this state. They stay Approved (owner decision) and are issued if the evaluation
    still arrives."""
    return (certificate.status == Status.APPROVED and certificate.recipient_type == RecipientType.REGISTRATION
            and not evaluated)


def pdf_state(certificate: Certificate, *, evaluated: bool, worker_running, now: datetime | None = None) -> str | None:
    """Why this certificate has no final PDF yet, or None (it has one, or it is not waiting for one). Only Approved
    certificates and reissued ones (updated_copy_pending) wait for a PDF. `worker_running`: a function, called only
    when needed (it asks the database whether qcluster is running)."""
    if certificate.file_path or not (certificate.status == Status.APPROVED or updated_copy_pending(certificate)):
        return None
    if approved_before_evaluation_rule(certificate, evaluated):
        closed = (now or timezone.now()) > certificate.activity.evaluation_deadline
        return PDF_PRE_RULE_CLOSED if closed else PDF_PRE_RULE_WAITING
    if certificate.render_failed_at:
        return PDF_FAILED
    return PDF_PREPARING if worker_running() else PDF_WORKER


WEASYPRINT_MISSING = ("The PDF tool (WeasyPrint) is not set up on the computer that runs qcluster. See \"Setup: "
                      "WeasyPrint on Windows\" in CLAUDE.md, then start qcluster again in a new terminal.")


def render_error_text(error: BaseException) -> str:
    """A failed render in plain words for CERTIFICATE.render_error (shown on the tab). Never the raw error message,
    which can contain file paths."""
    if isinstance(error, pdf.ImageUnreadable):
        text = str(error)
    elif isinstance(error, ImportError) or (isinstance(error, OSError) and "library" in str(error).lower()):
        text = WEASYPRINT_MISSING
    elif isinstance(error, pdf.NotAPdf):
        text = "The PDF tool returned something that is not a PDF."
    elif isinstance(error, FileNotFoundError):
        text = "A file needed for this certificate could not be found."
    else:
        text = (f"The PDF could not be created ({type(error).__name__}). Try again; if it fails again, look at the "
                "qcluster terminal.")
    return text[:255]


def record_render_failure(certificate_id: int, error: BaseException, now: datetime | None = None) -> None:
    """Remember that rendering failed (the background task and the daily retry call this): the tab shows "PDF failed —
    Retry" with the reason. Nothing is recorded once a PDF exists."""
    (Certificate.objects.filter(pk=certificate_id).filter(Q(file_path__isnull=True) | Q(file_path=""))
     .update(render_failed_at=now or timezone.now(), render_error=render_error_text(error)))


@transaction.atomic
def retry_certificate_pdfs(member: TBIMember, *, certificate_ids: list[int] | None = None,
                           activity: Activity | None = None) -> int:
    """"Retry" on a failed PDF (can_approve_certificates): the failure is cleared and the render is queued again. Only
    certificates whose PDF failed and that can be rendered now; all or nothing."""
    require_permission(member, Permission.APPROVE_CERTIFICATES)
    certificates = _select_for_action(certificate_ids, activity, [Status.APPROVED, Status.ISSUED],
                                      Q(render_failed_at__isnull=False), empty="No certificate PDFs of this activity failed.")

    def check(certificate: Certificate) -> None:
        if certificate.file_path:
            raise CertificateError("its PDF is already there.")
        if not certificate.render_failed_at:
            raise CertificateError("its PDF did not fail.")
        if reason := _not_ready_reason(certificate):
            raise CertificateError(f"it cannot be rendered now ({reason}).")

    _apply_all(certificates, check)
    Certificate.objects.filter(pk__in=[c.pk for c in certificates]).update(render_failed_at=None, render_error=None)
    for certificate in certificates:
        transaction.on_commit(lambda certificate_id=certificate.pk: queue_prepare(certificate_id), robust=True)
    logger.info("Member %s retried certificate PDFs %s.", member.pk, ", ".join(str(c.pk) for c in certificates))
    return len(certificates)


def queue_stalled_preparations(now: datetime | None = None) -> int:
    """The 5-minute job's catch-up (ASSUMPTIONS 368): a certificate that can be rendered, has no PDF and no recorded
    failure, approved (or reissued) more than STALLED_MINUTES ago, is queued again (a task that was never queued or
    got lost). prepare_certificate is safe to run twice. Returns how many were queued."""
    cutoff = (now or timezone.now()) - timedelta(minutes=STALLED_MINUTES)
    stalled = (pending_preparation().filter(render_failed_at__isnull=True)
               .filter(Q(status=Status.APPROVED, approved_at__lt=cutoff) | Q(status=Status.APPROVED, approved_at__isnull=True)
                       | Q(status=Status.ISSUED, reissued_at__lt=cutoff)))
    ids = list(stalled.values_list("pk", flat=True))
    for certificate_id in ids:
        queue_prepare(certificate_id)
    return len(ids)


def evaluation_submitted(registration_id: int, now: datetime | None = None) -> None:
    """Called after an evaluation is committed: a certificate waiting for approval is approved when its activity is
    approved (approve_ready_certificates, ASSUMPTIONS 369: then rendered, issued and emailed, no click); one already
    Approved (before the evaluation rule) is queued."""
    certificate = Certificate.objects.filter(registration_id=registration_id).select_related("activity").first()
    if certificate is None:
        return
    if certificate.status == Status.GENERATED:
        approve_ready_certificates(certificate.activity, certificate_ids=[certificate.pk], now=now)
    elif _not_ready_reason(certificate) is None:
        queue_prepare(certificate.pk)


def preview_name(certificate: Certificate) -> tuple[str, bool]:
    """(name shown on the preview, final?). The printed name of a participant comes from the evaluation; until it
    is submitted the preview shows the registration name (final=False: the page says so, outside the certificate)."""
    name = recipient_name(certificate)
    if name is not None:
        return name, True
    return certificate.registration.name, False


def preview_marked(certificate: Certificate) -> bool:
    """A small "PREVIEW" mark in a corner until the certificate is Approved (owner decision, ASSUMPTIONS 358)."""
    return certificate.status == Status.GENERATED


def preview_pdf(member: TBIMember, certificate: Certificate) -> bytes:
    """The certificate exactly as it will be printed, rendered now and never stored: the same template as the final
    PDF, with the name, activity, dates, number and both signature images (a missing image is left blank). Marked
    "PREVIEW" in a corner while it waits for approval. Any active staff member (ASSUMPTIONS 358)."""
    require_staff_member(member)
    return pdf.render_certificate_pdf(certificate, name=preview_name(certificate)[0], mark=preview_marked(certificate),
                                      missing_signature_ok=True)


# --- Email ---------------------------------------------------------------------------------------


def send_certificate(certificate_id: int, now: datetime | None = None) -> str:
    """Email an Issued participant certificate (PDF attached) to the evaluation's certificate_email.

    The row stays locked while sending, so a second task waits and then sees sent_at: a
    certificate is never sent twice. sent_at is set only after the mailer accepted the email (and clears
    send_failed_at). Raises if sending fails, after recording send_failed_at (the tab shows "Send failed").
    """
    try:
        return _send_certificate(certificate_id, now)
    except Exception:
        Certificate.objects.filter(pk=certificate_id, sent_at__isnull=True).update(send_failed_at=now or timezone.now())
        raise


def _send_certificate(certificate_id: int, now: datetime | None) -> str:
    with transaction.atomic():
        certificate = (
            Certificate.objects.select_for_update(of=("self",))  # lock only the certificate row
            .select_related("activity", "evaluation_submission")
            .filter(pk=certificate_id).first()
        )
        if certificate is None:  # deleted since it was queued (ASSUMPTIONS 389): nothing to send, no retry
            return CERTIFICATE_GONE
        if certificate.delivery_method != DeliveryMethod.EMAIL:
            return "hard copy: never emailed"
        if certificate.status != Status.ISSUED:
            return "not Issued"
        if certificate.sent_at is not None:
            return "already sent"
        if not certificate.file_path:
            return "PDF not ready (updated copy being prepared)"
        evaluation = certificate.evaluation_submission
        with certificate.file_path.open("rb") as handle:
            content = handle.read()
        emails.send_certificate_email(certificate, evaluation.certificate_name, evaluation.certificate_email, content)
        certificate.sent_at = now or timezone.now()
        certificate.send_failed_at = None
        certificate.save(update_fields=["sent_at", "send_failed_at"])
    return "sent"


# --- Background tasks: queueing and the daily retry ----------------------------------------------


def queue_prepare(certificate_id: int) -> None:
    from django_q.tasks import async_task

    async_task("certificates.tasks.run_prepare_certificate", certificate_id, task_name=f"Prepare certificate {certificate_id}")


def queue_send(certificate_id: int, attempt: int = 1) -> None:
    from django_q.tasks import async_task

    async_task("certificates.tasks.run_send_certificate", certificate_id, attempt, task_name=f"Email certificate {certificate_id}")


def schedule_send_retry(certificate_id: int, attempt: int, minutes: int) -> None:
    """Try sending again later (a one-off Django-Q2 schedule, deleted after it runs)."""
    from django_q.models import Schedule
    from django_q.tasks import schedule

    schedule(
        "certificates.tasks.run_send_certificate",
        certificate_id,
        attempt,
        name=f"Retry certificate email {certificate_id} (attempt {attempt}, {timezone.now():%Y%m%d%H%M%S%f})",
        schedule_type=Schedule.ONCE,
        repeats=-1,
        next_run=timezone.now() + timedelta(minutes=minutes),
    )


def pending_preparation() -> QuerySet:
    """Certificates whose PDF can be rendered now: Approved (resource persons, and participants with a submitted
    evaluation) and reissued ones waiting for their updated copy (ASSUMPTIONS 364). Failed renders are included (the
    daily retry tries them again, ASSUMPTIONS 368); participants approved before the evaluation rule without an
    evaluation cannot be rendered (no printed name) and are not."""
    no_file = Q(file_path__isnull=True) | Q(file_path="")
    approved = Q(status=Status.APPROVED) & (
        Q(recipient_type=RecipientType.RESOURCE_PERSON) | Q(registration__evaluation_submission__isnull=False))
    reissued = Q(status=Status.ISSUED, reissued_at__isnull=False)
    return Certificate.objects.filter(no_file).filter(approved | reissued)


def pending_emails() -> QuerySet:
    return (Certificate.objects.filter(status=Status.ISSUED, delivery_method=DeliveryMethod.EMAIL, sent_at__isnull=True)
            .exclude(Q(file_path__isnull=True) | Q(file_path="")))


def retry_pending_certificates() -> dict[str, int]:
    """The daily retry (8:30 AM Manila): render/issue what is ready, then send what is unsent.
    One failure never stops the others; each is logged by ID only."""
    result = {"prepared": 0, "prepare_failed": 0, "sent": 0, "send_failed": 0}
    for certificate_id in pending_preparation().values_list("pk", flat=True):
        try:
            if prepare_certificate(certificate_id) in ("rendered", "issued", "reissued"):
                result["prepared"] += 1
        except Exception as error:
            result["prepare_failed"] += 1
            record_render_failure(certificate_id, error)
            logger.error("Preparing certificate %s failed: %s.", certificate_id, type(error).__name__)
    for certificate_id in pending_emails().values_list("pk", flat=True):
        try:
            if send_certificate(certificate_id) == "sent":
                result["sent"] += 1
        except Exception as error:
            result["send_failed"] += 1
            logger.error("Sending certificate %s failed: %s.", certificate_id, type(error).__name__)
    return result


# --- Reading: staff list, startup portal ------------------------------------------------------


def _filtered_certificates(filters: dict, with_status: bool = True) -> QuerySet:
    queryset = Certificate.objects.select_related("activity", "registration", "resource_person", "evaluation_submission", "left_signatory", "right_signatory")
    if filters.get("activity"):
        queryset = queryset.filter(activity_id=filters["activity"])
    if with_status and filters.get("status"):
        if filters["status"] not in Status.values:
            raise CertificateError(f"status must be one of: {', '.join(Status.values)}.")
        queryset = queryset.filter(status=filters["status"])
    if filters.get("recipient_type"):
        if filters["recipient_type"] not in RecipientType.values:
            raise CertificateError(f"recipient_type must be one of: {', '.join(RecipientType.values)}.")
        queryset = queryset.filter(recipient_type=filters["recipient_type"])
    if filters.get("delivery_method"):
        if filters["delivery_method"] not in DeliveryMethod.values:
            raise CertificateError(f"delivery_method must be one of: {', '.join(DeliveryMethod.values)}.")
        queryset = queryset.filter(delivery_method=filters["delivery_method"])
    if (filters.get("q") or "").strip():
        queryset = queryset.filter(search_q(filters["q"]))
    return queryset


def search_q(text: str) -> Q:
    """The Certificates page's search (ASSUMPTIONS 372): every word must appear in the certificate number, the registered
    name, the name or email typed in the evaluation, the resource person's name or the activity title (any case). No
    join that multiplies rows (the evaluation is an EXISTS)."""
    query = Q()
    for word in text.split()[:10]:
        typed = EvaluationSubmission.objects.filter(registration_id=OuterRef("registration_id")).filter(
            Q(certificate_name__icontains=word) | Q(certificate_email__icontains=word))
        query &= (Q(certificate_number__icontains=word) | Q(registration__name__icontains=word)
                  | Q(resource_person__name__icontains=word) | Q(activity__title__icontains=word) | Exists(typed))
    return query


def list_certificates(filters: dict | None = None, page: int | str | None = 1, per_page: int = DEFAULT_PAGE_SIZE) -> PageResult:
    """Staff list. Filters (optional): activity (ID), status, recipient_type, delivery_method. By activity, then number."""
    queryset = _filtered_certificates(filters or {})
    return _paginate(queryset.order_by("activity_id", "certificate_number"), page, per_page)


def list_certificates_newest(filters: dict | None = None, page: int | str | None = 1,
                             per_page: int = DEFAULT_PAGE_SIZE) -> PageResult:
    """The Certificates page's "All certificates (newest first)" view (ASSUMPTIONS 372): the newest number first."""
    queryset = _filtered_certificates(filters or {})
    return _paginate(queryset.order_by("-certificate_number", "-pk"), page, per_page)


# Newest activity first by its END (ASSUMPTIONS 372): end date, then end time; an activity without times counts as the
# whole day (it ends at midnight), so it comes before the timed ones of that day in this newest-first order.
NEWEST_ACTIVITY_FIRST = (F("end_date").desc(), F("end_time").desc(nulls_first=True), F("pk").desc())
ACTIVITIES_PER_PAGE = 10


@dataclass
class ActivityCertificates:
    """One activity group of the Certificates page: the activity, its certificates matching every filter (newest number
    first) and its counts per status (every filter except the status, as the page's status counts)."""

    activity: Activity
    certificates: list
    counts: dict


def certificates_by_activity(filters: dict | None = None, page: int | str | None = 1,
                             per_page: int = ACTIVITIES_PER_PAGE) -> PageResult:
    """The Certificates page's "By activity" view (ASSUMPTIONS 372). Pages count ACTIVITIES (never splits one); only
    activities with at least one certificate matching the filters; NEWEST_ACTIVITY_FIRST. Three queries for the page
    whatever its size (plus the pager's count): the activities, their certificates, their counts."""
    filters = filters or {}
    matching = _filtered_certificates(filters)
    activities = (Activity.objects.filter(pk__in=matching.order_by().values("activity_id"))
                  .select_related("certificates_approved_by").order_by(*NEWEST_ACTIVITY_FIRST))
    result = _paginate(activities, page, per_page)
    ids = [activity.pk for activity in result.items]
    by_activity: dict[int, list] = {pk: [] for pk in ids}
    for certificate in matching.filter(activity_id__in=ids).order_by("-certificate_number", "-pk"):
        by_activity[certificate.activity_id].append(certificate)
    counts = {pk: dict.fromkeys(Status.values, 0) for pk in ids}
    rows = (_filtered_certificates({**filters, "status": ""}, with_status=False).filter(activity_id__in=ids).order_by()
            .values("activity_id", "status").annotate(n=Count("pk")))
    for row in rows:
        counts[row["activity_id"]][row["status"]] = row["n"]
    result.items = [ActivityCertificates(activity, by_activity[activity.pk], counts[activity.pk]) for activity in result.items]
    return result


def certificate_status_counts(filters: dict | None = None) -> dict[str, int]:
    """{status: count} for every status (0 when none), with the same filters as list_certificates except
    the status itself (the Certificates page shows them next to the status filter). One query."""
    counts = dict.fromkeys(Status.values, 0)
    rows = _filtered_certificates(filters or {}, with_status=False).order_by().values("status").annotate(n=Count("pk"))
    counts.update({row["status"]: row["n"] for row in rows})
    return counts


def certificates_for_startup(startup) -> QuerySet:
    """"My certificates": Issued certificates of the startup's registrations (linked or matched by
    email, as in Step 7), never by certificate_email."""
    from startups.services import registrations_for

    return (
        Certificate.objects.filter(status=Status.ISSUED, registration__in=registrations_for(startup).values("pk"))
        .select_related("activity")
        .order_by("-issued_at", "-pk")
    )


# --- Evidence documents (DOCUMENT.related_certificate, owner decision Q1 = B) ---------------------


def upload_evidence(member: TBIMember, certificate: Certificate, file: UploadedFile | None, title: str = "") -> Document:
    """Optional scan for a certificate (a wet-signed hard copy, an acknowledgment receipt). Same rules
    as any Step 6 upload (any active staff member, file checks, naming pattern, For Review, then the
    Step 6 review). It appears only with its certificate, never in the checklist or repository."""
    document_type = DocumentType.objects.filter(name=CERTIFICATE_DOCUMENT_TYPE).first()
    if document_type is None:
        raise CertificateError(f'The "{CERTIFICATE_DOCUMENT_TYPE}" document type is missing. Run python manage.py seed_data.')
    return document_services.upload_document(
        member, certificate.activity, document_type, file, title=title or f"Certificate {certificate.certificate_number}",
        related_certificate=certificate,
    )


def evidence_for(certificate: Certificate) -> QuerySet:
    return Document.objects.certificate_documents().filter(related_certificate=certificate).select_related("status", "uploaded_by")
