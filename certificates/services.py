"""Business logic for certificates: design (template and signatories), generating, approval,
the final PDF, issuing and emailing. Views, the Django admin and tasks call these functions.

The life of a certificate (status rules in set_certificate_status, ASSUMPTIONS 357):

    Generated ("Waiting for approval") --Director approves--> Approved --> Issued
              <------------ regenerate (new design, approval cleared) ------

- Generate / regenerate / hand over / resend: can_create_activity. Approve and add signatures: can_approve_certificates
  (the Director, both Project Heads and the System Administrator, ASSUMPTIONS 361), one certificate or all of an
  activity at once. Approval is refused while a signature image is missing ("Add the VP's signature first.").
- Adding or replacing a signature on the activity's Certificates tab (upload_signature) updates that activity's
  certificates that are not Issued: the PDF is rendered again and Approved ones wait for approval again.

- Participant (Certificate of Participation, by email): the name printed and the address come
  from the participant's EVALUATION_SUBMISSION. When the certificate is Approved AND the evaluation
  is submitted (whichever happens last), a background task renders the PDF, issues the certificate
  and queues the email.
- Resource person (Certificate of Appreciation, hard copy): the PDF is rendered when it is
  Approved; staff mark it Issued when the hard copy is handed over. Never emailed.
"""

import logging
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import connection, transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone
from PIL import Image

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission, require_staff_member
from config.files import write_file_atomically
from activities.models import Activity, ActivityStatus
from activities.services import PageResult, _paginate
from documents import services as document_services
from documents.models import Document, DocumentType
from registrations.models import EvaluationSubmission

from . import emails, pdf
from .models import Certificate, CertificateSignatory, CertificateTemplate

logger = logging.getLogger("certificates")
audit_logger = logging.getLogger("certificates.audit")  # who added or replaced a signature (member IDs only)

Status = Certificate.Status
RecipientType = Certificate.RecipientType
DeliveryMethod = Certificate.DeliveryMethod
Position = CertificateSignatory.Position

DEFAULT_PAGE_SIZE = 20

# The only status moves allowed. Forward one step at a time, plus one backward move:
# Approved -> Generated when the certificate is regenerated (it must be approved again).
# Issued is final: a wrong certificate is replaced by a new one with a new number.
ALLOWED_MOVES: dict[str, set[str]] = {
    Status.GENERATED: {Status.APPROVED},
    Status.APPROVED: {Status.ISSUED, Status.GENERATED},
    Status.ISSUED: set(),
}
SIGNATORY_LABELS = {Position.VP: "VP", Position.DIRECTOR: "Director"}
SIGNATORY_FIELDS = {Position.VP: "vp_signatory", Position.DIRECTOR: "director_signatory"}  # CERTIFICATE columns

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


# --- Templates and signatories (Admin, can_manage_users) ----------------------------------------
# Files never change after upload: a new design or signature is a new row, so existing certificates
# keep exactly what they were generated with. Nothing is deleted (PROTECT); rows are deactivated.


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


@transaction.atomic
def add_signatory(member: TBIMember, name: str, position: str, file: UploadedFile | None) -> CertificateSignatory:
    """Add the VP or Director, with their signature (PNG or JPG) or without it (added later on an activity's
    Certificates tab, ASSUMPTIONS 361). They become the active signatory for that position; the previous one is
    deactivated in the same transaction."""
    require_permission(member, Permission.MANAGE_USERS)
    name = _clean_name(name)
    if position not in Position.values:
        raise ValidationError({"position": ["Choose vp or director."]})
    if file is not None:
        validate_signature_image(file)
    list(CertificateSignatory.objects.select_for_update().filter(position=position, is_active=True))
    CertificateSignatory.objects.filter(position=position, is_active=True).update(is_active=False)
    signatory = CertificateSignatory(name=name, position=position, is_active=True)
    if file is not None:
        signatory.signature_image_path.save(file.name, file, save=False)
    try:
        signatory.save()
    except Exception:
        _delete_file(signatory.signature_image_path)
        raise
    if file is not None:
        audit_logger.info("Signature added: position=%s signatory_id=%s (Admin Console) by member_id=%s",
                          position, signatory.pk, member.pk)
    return signatory


def deactivate_signatory(member: TBIMember, signatory: CertificateSignatory) -> CertificateSignatory:
    require_permission(member, Permission.MANAGE_USERS)
    CertificateSignatory.objects.filter(pk=signatory.pk).update(is_active=False)
    signatory.refresh_from_db()
    return signatory


def active_design() -> tuple[CertificateTemplate, CertificateSignatory, CertificateSignatory]:
    """(template, VP, Director) that are active now. Refused, naming what is missing."""
    template = CertificateTemplate.objects.filter(is_active=True).first()
    vp = CertificateSignatory.objects.filter(position=Position.VP, is_active=True).first()
    director = CertificateSignatory.objects.filter(position=Position.DIRECTOR, is_active=True).first()
    missing = [label for label, row in (("certificate template", template), ("VP signatory", vp), ("Director signatory", director)) if row is None]
    if missing:
        raise CertificateError(f"Cannot generate certificates: there is no active {', no active '.join(missing)}. The System Administrator must add it in the Admin Console first.")
    return template, vp, director


# --- Certificate numbers ------------------------------------------------------------------------


def _certificate_numbers(now: datetime | None = None):
    """Yield TBI-<Manila year>-<5-digit sequence> numbers, restarting at 00001 each year.

    Must run inside a transaction: a PostgreSQL advisory lock (held until commit) makes other
    generations wait, so two generations at the same moment never get the same number. The
    unique constraint on certificate_number is the final guard.
    """
    year = timezone.localtime(now or timezone.now()).year
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [NUMBER_LOCK_KEY])
    prefix = f"TBI-{year}-"
    existing = Certificate.objects.filter(certificate_number__startswith=prefix).values_list("certificate_number", flat=True)
    last = max((int(n[len(prefix):]) for n in existing if n[len(prefix):].isdigit()), default=0)
    while True:
        last += 1
        yield f"{prefix}{last:05d}"


# --- Generating (can_create_activity) ------------------------------------------------------------


NOT_CONDUCTED = "Certificates can be generated only for Conducted activities."


def generation_closed_reason(activity: Activity) -> str | None:
    """None when certificates can be generated for `activity` now; otherwise why not (not Conducted, or the
    template / VP / Director that active_design() names as missing). Read only: the Certificates tab shows it
    next to the button, and generate_certificates refuses with the same message."""
    if activity.status.name != ActivityStatus.CONDUCTED:
        return NOT_CONDUCTED
    try:
        active_design()
    except CertificateError as error:
        return str(error)
    return None


@transaction.atomic
def generate_certificates(member: TBIMember, activity: Activity, now: datetime | None = None) -> dict[str, int]:
    """Create the missing certificates of a Conducted activity. Safe to run twice.

    - One Certificate of Participation (email) per registration with attendance confirmed.
    - One Certificate of Appreciation (hard copy) per linked resource person.
    Each records the template and the VP and Director that are active now.
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    locked = Activity.objects.select_for_update().select_related("status").get(pk=activity.pk)
    if reason := generation_closed_reason(locked):
        raise CertificateError(reason)
    template, vp, director = active_design()

    registrations = locked.registrations.filter(attendance_confirmed=True, certificates__isnull=True).order_by("name", "pk")
    has_certificate = Certificate.objects.filter(activity=locked, resource_person__isnull=False).values("resource_person")
    links = (
        locked.resource_person_links.exclude(resource_person__in=has_certificate)
        .select_related("resource_person")
        .order_by("resource_person__name", "pk")
    )
    numbers = _certificate_numbers(now)
    common = {"certificate_template": template, "activity": locked, "vp_signatory": vp, "director_signatory": director}
    participation = appreciation = 0
    for registration in registrations:
        Certificate.objects.create(
            certificate_number=next(numbers), recipient_type=RecipientType.REGISTRATION, registration=registration,
            delivery_method=DeliveryMethod.EMAIL, **common,
        )
        participation += 1
    for link in links:
        Certificate.objects.create(
            certificate_number=next(numbers), recipient_type=RecipientType.RESOURCE_PERSON,
            resource_person=link.resource_person, delivery_method=DeliveryMethod.HARD_COPY, **common,
        )
        appreciation += 1
    return {
        "participation_created": participation,
        "appreciation_created": appreciation,
        "total": Certificate.objects.filter(activity=locked).count(),
    }


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


def add_signature_reason(position: str) -> str:
    """ "Add the VP's signature first." (the reason approval is refused, ASSUMPTIONS 361)."""
    return f"Add the {SIGNATORY_LABELS[position]}'s signature first."


def missing_signature_reason(signatories) -> str | None:
    """None when every given signatory has a signature image; otherwise the reason approval is refused (VP first).
    A certificate keeps the signatories it was generated with; adding or replacing a signature on the activity's
    Certificates tab updates them (upload_signature)."""
    for signatory in sorted({s.pk: s for s in signatories}.values(), key=lambda s: s.position != Position.VP):
        if not signature_present(signatory):
            return add_signature_reason(signatory.position)
    return None


def _certificate_signatories(certificates) -> list[CertificateSignatory]:
    return [person for c in certificates for person in (c.vp_signatory, c.director_signatory)]


def approval_closed_reason(activity: Activity) -> str | None:
    """Why "Approve all for this activity" cannot be used now, or None. Read only: the Certificates tab shows it
    next to the button, and approve_certificates refuses with the same message."""
    waiting = list(
        Certificate.objects.filter(activity=activity, status=Status.GENERATED).select_related("vp_signatory", "director_signatory")
    )
    if not waiting:
        return "No certificates of this activity are waiting for approval."
    return missing_signature_reason(_certificate_signatories(waiting))


def set_certificate_status(
    certificate: Certificate, new_status: str, *, member: TBIMember | None = None, now: datetime | None = None
) -> Certificate:
    """Move a certificate to `new_status` after checking the rules, then save it.

    - Only the moves in ALLOWED_MOVES are possible.
    - Approved needs both signature images (missing_signature_reason); it sets approved_by and approved_at.
    - Back to Generated (regenerated): clears approved_by and approved_at.
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
        if reason := missing_signature_reason([certificate.vp_signatory, certificate.director_signatory]):
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


# --- Signatures on the activity's Certificates tab (can_approve_certificates, ASSUMPTIONS 361) -----


def activity_signatories(activity: Activity) -> dict[str, CertificateSignatory | None]:
    """Per position (vp, director): the signatory this activity's certificates that are not Issued use (the newest
    one if they differ), otherwise the active one (the one Generate will use). None: no active signatory at all."""
    result: dict[str, CertificateSignatory | None] = {}
    pending = Certificate.objects.filter(activity=activity).exclude(status=Status.ISSUED)
    for position, field in SIGNATORY_FIELDS.items():
        signatory_id = pending.order_by(f"-{field}_id").values_list(f"{field}_id", flat=True).first()
        if signatory_id is not None:
            result[position] = CertificateSignatory.objects.get(pk=signatory_id)
        else:
            result[position] = CertificateSignatory.objects.filter(position=position, is_active=True).first()
    return result


def no_signatory_reason(position: str) -> str:
    return (f"There is no active {SIGNATORY_LABELS[position]} signatory yet. "
            "The System Administrator adds one in the Admin Console first.")


def signature_closed_reason(activity: Activity, position: str) -> str | None:
    """Why a signature cannot be added for `position` now, or None (the tab shows it instead of the upload)."""
    if activity_signatories(activity).get(position) is None:
        return no_signatory_reason(position)
    return None


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
        certificate.save(update_fields=[*changes, "file_path"])
        if certificate.status == Status.APPROVED:
            set_certificate_status(certificate, Status.GENERATED, member=member, now=now)


@transaction.atomic
def upload_signature(member: TBIMember, activity: Activity, position: str, file: UploadedFile | None,
                     now: datetime | None = None) -> dict:
    """Add or replace the VP's or Director's signature image (PNG or JPG) from the activity's Certificates tab.

    - Add (the signatory has no image yet): the image is stored on that signatory (every certificate using it,
      of any activity, gets it).
    - Replace: a new signatory row with the same name and position and the new image (an image never changes:
      other activities keep the one they were generated with); it is active if the old one was.
    Either way this activity's certificates that are not Issued use it from now on: their PDF is rendered again and
    Approved ones wait for approval again. Logged on certificates.audit (member and signatory IDs only).
    Returns {"action": "added" | "replaced", "signatory": ..., "updated": <certificates updated>}.
    """
    require_permission(member, Permission.APPROVE_CERTIFICATES)
    if position not in SIGNATORY_FIELDS:
        raise ValidationError({"position": ["Choose vp or director."]})
    if reason := signature_closed_reason(activity, position):
        raise CertificateError(reason)
    validate_signature_image(file)
    current = CertificateSignatory.objects.select_for_update().get(pk=activity_signatories(activity)[position].pk)
    field = SIGNATORY_FIELDS[position]
    # Certificates of an older signatory than the one shown are left alone (activity_signatories shows the newest).
    certificates = list(
        Certificate.objects.select_for_update(of=("self",)).filter(activity=activity, **{field: current})
        .exclude(status=Status.ISSUED).order_by("pk")
    )
    stored = None
    try:
        if not current.signature_image_path:
            action, signatory = "added", current
            signatory.signature_image_path.save(file.name, file, save=False)
            stored = signatory.signature_image_path.name
            signatory.save(update_fields=["signature_image_path"])
        else:
            action = "replaced"
            if current.is_active:
                CertificateSignatory.objects.filter(pk=current.pk).update(is_active=False)
            signatory = CertificateSignatory(name=current.name, position=position, is_active=current.is_active)
            signatory.signature_image_path.save(file.name, file, save=False)
            stored = signatory.signature_image_path.name
            signatory.save()
        _refresh_certificates(certificates, member, now, **{field: signatory})
    except BaseException:
        if stored:
            CertificateSignatory._meta.get_field("signature_image_path").storage.delete(stored)
        raise
    audit_logger.info(
        "Signature %s: position=%s signatory_id=%s -> %s activity_id=%s certificates_updated=%s by member_id=%s",
        action, position, current.pk, signatory.pk, activity.pk, len(certificates), member.pk,
    )
    return {"action": action, "signatory": signatory, "updated": len(certificates)}


# --- Actions: one certificate, a list, or all of an activity --------------------------------------


def _select_for_action(certificate_ids: list[int] | None, activity: Activity | None, statuses, extra: Q | None = None,
                       empty: str = "") -> list[Certificate]:
    """Lock the certificates to act on: the given IDs, or every certificate of `activity` in one of `statuses`
    (and matching `extra`). Must be called inside a transaction."""
    if (certificate_ids is None) == (activity is None):
        raise CertificateError("Give either certificate_ids or activity_id.")
    statuses = [statuses] if isinstance(statuses, str) else list(statuses)
    queryset = Certificate.objects.select_for_update(of=("self",)).select_related("vp_signatory", "director_signatory").order_by("pk")
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
def approve_certificates(member: TBIMember, *, certificate_ids: list[int] | None = None, activity: Activity | None = None, now: datetime | None = None) -> int:
    """Generated -> Approved (can_approve_certificates: the Director, both Project Heads, the System Administrator). `activity` = "Approve all for this activity":
    every certificate waiting for approval, all or nothing. Refused while a signature image is missing (one clear
    reason for the whole action).

    Approval means the certificate (design, wording, activity details, signatures) is approved. After the commit a
    background task renders the PDF of each certificate that is ready: resource persons at once; participants once
    their evaluation is in (then issued and emailed).
    """
    require_permission(member, Permission.APPROVE_CERTIFICATES)
    certificates = _select_for_action(certificate_ids, activity, Status.GENERATED,
                                      empty="No certificates of this activity are waiting for approval.")
    if reason := missing_signature_reason(_certificate_signatories(certificates)):
        raise CertificateError(reason)
    _apply_all(certificates, lambda c: set_certificate_status(c, Status.APPROVED, member=member, now=now))
    logger.info("Member %s approved certificates %s.", member.pk, ", ".join(str(c.pk) for c in certificates))
    for certificate in certificates:
        if _not_ready_reason(certificate) is None:
            # robust=True: a problem queueing never undoes the approval (the daily retry catches it).
            transaction.on_commit(lambda certificate_id=certificate.pk: queue_prepare(certificate_id), robust=True)
    return len(certificates)


@transaction.atomic
def regenerate_certificates(member: TBIMember, *, certificate_ids: list[int] | None = None, activity: Activity | None = None, now: datetime | None = None) -> int:
    """Apply the design (template, VP, Director) that is active now to certificates that are not Issued, remove
    their rendered PDF and send them back to "Waiting for approval" (they are approved again). The number
    stays. Issued certificates are final (item 46): refused."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    certificates = _select_for_action(certificate_ids, activity, [Status.GENERATED, Status.APPROVED],
                                      empty="This activity has no certificates to regenerate (Issued certificates are final).")
    template, vp, director = active_design()

    def regenerate(certificate: Certificate) -> None:
        if certificate.status == Status.ISSUED:
            raise CertificateError("Issued certificates are final and cannot be regenerated.")
        _refresh_certificates([certificate], member, now, certificate_template=template, vp_signatory=vp,
                              director_signatory=director)

    _apply_all(certificates, regenerate)
    logger.info("Member %s regenerated certificates %s.", member.pk, ", ".join(str(c.pk) for c in certificates))
    return len(certificates)


@transaction.atomic
def mark_handed_over(member: TBIMember, *, certificate_ids: list[int] | None = None, activity: Activity | None = None, now: datetime | None = None) -> int:
    """Approved -> Issued for hard-copy certificates (resource persons), when the printed
    certificate is handed over. Needs the rendered PDF (staff print it). Never emailed."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    certificates = _select_for_action(certificate_ids, activity, Status.APPROVED, Q(delivery_method=DeliveryMethod.HARD_COPY))

    def hand_over(certificate: Certificate) -> None:
        if certificate.delivery_method != DeliveryMethod.HARD_COPY:
            raise CertificateError("only hard-copy certificates are handed over; participant certificates are issued automatically.")
        if certificate.status == Status.APPROVED and not certificate.file_path:
            raise CertificateError("the PDF is not ready yet. Try again in a moment.")
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
    """None when the final PDF can be rendered now; otherwise why not."""
    if certificate.status != Status.APPROVED:
        return "not Approved"
    if certificate.file_path:
        return "PDF already rendered"
    if certificate.recipient_type == RecipientType.REGISTRATION and get_submitted_evaluation(certificate) is None:
        return "waiting for the evaluation"
    return None


def _design(certificate: Certificate) -> tuple[int, int, int]:
    return certificate.certificate_template_id, certificate.vp_signatory_id, certificate.director_signatory_id


def final_pdf_name(certificate: Certificate) -> str:
    """certificates/<year from the number>/<number>.pdf"""
    year = certificate.certificate_number.split("-")[1]
    return f"certificates/{year}/{certificate.certificate_number}.pdf"


def _write_file_atomically(name: str, content: bytes) -> None:
    """The final file is either complete or absent, never half-written (config.files)."""
    write_file_atomically(Certificate._meta.get_field("file_path").storage, name, content)


def prepare_certificate(certificate_id: int, now: datetime | None = None) -> str:
    """Render the final PDF of an Approved certificate that is ready; issue a participant's.

    Returns "rendered", "issued" or the reason nothing was done. Raises if rendering or saving fails
    (the task logs it); then no file is left and the certificate stays Approved for a retry.
    """
    certificate = Certificate.objects.select_related(
        "activity", "certificate_template", "vp_signatory", "director_signatory", "resource_person"
    ).get(pk=certificate_id)
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
            locked.save(update_fields=["file_path"])
            if locked.recipient_type == RecipientType.REGISTRATION:
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


def evaluation_submitted(registration_id: int) -> None:
    """Called after an evaluation is committed: queue the certificate if it is already Approved."""
    certificate = Certificate.objects.filter(registration_id=registration_id).first()
    if certificate is not None and _not_ready_reason(certificate) is None:
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
            .get(pk=certificate_id)
        )
        if certificate.delivery_method != DeliveryMethod.EMAIL:
            return "hard copy: never emailed"
        if certificate.status != Status.ISSUED:
            return "not Issued"
        if certificate.sent_at is not None:
            return "already sent"
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
    """Approved certificates whose PDF can be rendered now (resource persons, and participants
    with a submitted evaluation)."""
    return Certificate.objects.filter(status=Status.APPROVED).filter(Q(file_path__isnull=True) | Q(file_path="")).filter(
        Q(recipient_type=RecipientType.RESOURCE_PERSON) | Q(registration__evaluation_submission__isnull=False)
    )


def pending_emails() -> QuerySet:
    return Certificate.objects.filter(status=Status.ISSUED, delivery_method=DeliveryMethod.EMAIL, sent_at__isnull=True)


def retry_pending_certificates() -> dict[str, int]:
    """The daily retry (8:30 AM Manila): render/issue what is ready, then send what is unsent.
    One failure never stops the others; each is logged by ID only."""
    result = {"prepared": 0, "prepare_failed": 0, "sent": 0, "send_failed": 0}
    for certificate_id in pending_preparation().values_list("pk", flat=True):
        try:
            if prepare_certificate(certificate_id) in ("rendered", "issued"):
                result["prepared"] += 1
        except Exception as error:
            result["prepare_failed"] += 1
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
    queryset = Certificate.objects.select_related("activity", "registration", "resource_person", "evaluation_submission")
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
    return queryset


def list_certificates(filters: dict | None = None, page: int | str | None = 1, per_page: int = DEFAULT_PAGE_SIZE) -> PageResult:
    """Staff list. Filters (optional): activity (ID), status, recipient_type, delivery_method. By activity, then number."""
    queryset = _filtered_certificates(filters or {})
    return _paginate(queryset.order_by("activity_id", "certificate_number"), page, per_page)


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
