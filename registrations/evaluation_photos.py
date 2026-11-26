"""Participant photos with the evaluation (ASSUMPTIONS 435): the one place that checks, stores, shows, marks and
deletes them.

On the evaluation's review step a participant may add up to EVALUATION_PHOTO_MAX_FILES files (photos of themselves at
the event, or their work): JPG / PNG photos and PDF work outputs, each at most EVALUATION_PHOTO_MAX_MB, checked by name
AND real content like document uploads. They are part of the same submission (submit_evaluation calls
clean_attachments and store_attachments; one evaluation, never anonymous).
- Photos are re-encoded by Pillow: turned upright first (the phone's rotation), then saved fresh WITHOUT any metadata
  (EXIF, GPS location, camera), the long side at most EVALUATION_PHOTO_MAX_SIDE. PDFs are stored as they are (owner
  decision: no new library to clean PDF metadata; the privacy notice promises it for photos only).
- Stored under evaluation-photos/activity-<id>/<random>.<ext>; never served from /media/: staff with
  can_create_activity see them through registrations.page_views (evaluation_photo / evaluation_photo_download).
- "Use in reports" (default OFF, photos can show other people) is changed only by set_use_in_reports
  (can_create_activity), which keeps who and when and logs it on registrations.audit (IDs only).
- photos_for_reports() is the ONE query for reports (Prompts 6–7): ticked files only. Nothing here is a DOCUMENT, so
  the checklist, the payment packet and the AI index never see these files.
- Deleting a submission, a registration or an activity removes the files after the commit (post_delete receiver,
  connected in RegistrationsConfig.ready). No automatic retention yet (open question, CLAUDE.md).
"""

import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from accounts.permissions import Permission, require_permission_for

from .models import EvaluationAttachment, EvaluationSubmission

logger = logging.getLogger("registrations")
audit_log = logging.getLogger("registrations.audit")

FIELD = "photos"  # the file input's name and the error key
UPLOAD_ROOT = "evaluation-photos"
VIEW_PERMISSION = Permission.CREATE_ACTIVITY  # see the files, download them and tick "Use in reports"
MAX_SOURCE_PIXELS = 60_000_000  # refuse absurd images before decoding them (decompression bombs); a 50 MP phone fits
FILE_NAME_MAX = 255

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
PDF_SIGNATURE = b"%PDF-"
# Allowed extension -> (stored extension, content type)
EXTENSIONS = {
    "jpg": ("jpg", EvaluationAttachment.ContentType.JPEG),
    "jpeg": ("jpg", EvaluationAttachment.ContentType.JPEG),
    "png": ("png", EvaluationAttachment.ContentType.PNG),
    "pdf": ("pdf", EvaluationAttachment.ContentType.PDF),
}

FILE_TYPE = "“{name}”: only JPG or PNG photos and PDF files can be uploaded."
FILE_CONTENT = "“{name}” is not a valid {kind} file."
FILE_EMPTY = "“{name}” is empty."


def too_many_files() -> str:
    return f"Choose at most {settings.EVALUATION_PHOTO_MAX_FILES} files."


def file_too_large(name: str) -> str:
    return f"“{name}” is larger than {settings.EVALUATION_PHOTO_MAX_MB} MB. Choose a smaller file."


def limits_text() -> str:
    """The help line under the file input."""
    return (f"Optional. Up to {settings.EVALUATION_PHOTO_MAX_FILES} files, JPG or PNG photos or PDF, "
            f"at most {settings.EVALUATION_PHOTO_MAX_MB} MB each.")


@dataclass(frozen=True)
class CleanAttachment:
    """A checked file, ready to store: the bytes to write (re-encoded for photos) and what the row keeps."""

    file_name: str
    extension: str
    content_type: str
    content: bytes


def _refuse(message: str):
    raise ValidationError({FIELD: [message]})


def display_name(raw: str) -> str:
    """The participant's file name for display only: no folders, no control characters, at most 255 characters
    (the extension kept). Never used in a stored path."""
    name = re.split(r"[\\/]", raw or "")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip() or "file"
    if len(name) > FILE_NAME_MAX:
        stem, dot, extension = name.rpartition(".")
        extension = extension[:10] if dot else ""
        name = stem[: FILE_NAME_MAX - len(extension) - 1] + "." + extension if extension else name[:FILE_NAME_MAX]
    return name


def clean_attachments(files) -> list[CleanAttachment]:
    """Check every chosen file (none is fine) and re-encode the photos. Raises ValidationError({"photos": [...]}) with
    the first problem; nothing is stored here. The one check of participant photos."""
    files = [file for file in (files or []) if file is not None and getattr(file, "name", "")]
    if len(files) > settings.EVALUATION_PHOTO_MAX_FILES:
        _refuse(too_many_files())
    return [_clean_one(file) for file in files]


def _clean_one(file) -> CleanAttachment:
    name = display_name(file.name)
    extension = name.rpartition(".")[2].lower() if "." in name else ""
    if extension not in EXTENSIONS:
        _refuse(FILE_TYPE.format(name=name))
    stored_extension, content_type = EXTENSIONS[extension]
    size = getattr(file, "size", None) or 0
    if size > settings.EVALUATION_PHOTO_MAX_MB * 1024 * 1024:
        _refuse(file_too_large(name))
    file.seek(0)
    content = file.read(settings.EVALUATION_PHOTO_MAX_MB * 1024 * 1024 + 1)
    file.seek(0)
    if not content:
        _refuse(FILE_EMPTY.format(name=name))
    if len(content) > settings.EVALUATION_PHOTO_MAX_MB * 1024 * 1024:
        _refuse(file_too_large(name))
    if stored_extension == "pdf":
        if not content.startswith(PDF_SIGNATURE):
            _refuse(FILE_CONTENT.format(name=name, kind="PDF"))
        return CleanAttachment(name, "pdf", content_type, content)
    expected = JPEG_SIGNATURE if stored_extension == "jpg" else PNG_SIGNATURE
    if not content.startswith(expected):
        _refuse(FILE_CONTENT.format(name=name, kind=stored_extension.upper()))
    return CleanAttachment(name, stored_extension, content_type, reencode(content, stored_extension, name))


def reencode(content: bytes, extension: str, name: str = "file") -> bytes:
    """Open with Pillow (JPEG / PNG only), turn it upright (EXIF orientation), shrink the long side to
    EVALUATION_PHOTO_MAX_SIDE and save a fresh file of the same type WITHOUT metadata (no EXIF, no GPS, no text chunks;
    only the colour profile is kept so colours look right)."""
    kind = "JPG" if extension == "jpg" else "PNG"
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format != ("JPEG" if extension == "jpg" else "PNG"):
                _refuse(FILE_CONTENT.format(name=name, kind=kind))
            width, height = source.size
            if not width or not height or width * height > MAX_SOURCE_PIXELS:
                _refuse(FILE_CONTENT.format(name=name, kind=kind))
            icc_profile = source.info.get("icc_profile")
            image = ImageOps.exif_transpose(source)
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError, ValueError):
        _refuse(FILE_CONTENT.format(name=name, kind=kind))
    side = settings.EVALUATION_PHOTO_MAX_SIDE
    image.thumbnail((side, side))
    buffer = BytesIO()
    extra = {"icc_profile": icc_profile} if icc_profile else {}
    if extension == "jpg":
        image.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True, **extra)
    else:
        mode = "RGBA" if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info else "RGB"
        image.convert(mode).save(buffer, format="PNG", optimize=True, **extra)
    return buffer.getvalue()


def store_attachments(
    submission: EvaluationSubmission, cleaned: list[CleanAttachment], written: list[str], now: datetime | None = None
) -> list[EvaluationAttachment]:
    """Write the checked files under a random name and create their rows (inside submit_evaluation's transaction).
    Each stored name is added to `written` first, so the caller removes the files when its transaction fails."""
    now = now or timezone.now()
    activity_id = submission.registration.activity_id
    rows: list[EvaluationAttachment] = []
    for clean in cleaned:
        name = default_storage.save(f"{UPLOAD_ROOT}/activity-{activity_id}/{uuid.uuid4().hex}.{clean.extension}",
                                    ContentFile(clean.content))
        written.append(name)
        rows.append(EvaluationAttachment.objects.create(
            submission=submission, file_path=name, file_name=clean.file_name,
            file_size_kb=max(1, math.ceil(len(clean.content) / 1024)), content_type=clean.content_type,
            uploaded_at=now,
        ))
    if rows:
        audit_log.info("Evaluation photos uploaded: %s file(s), submission %s, activity %s.", len(rows), submission.pk,
                       activity_id)
    return rows


# --- Staff: reading and marking --------------------------------------------------------------------------------------


def attachments_for(activity) -> QuerySet:
    """Every participant file of an activity, oldest first, with the person (the Evaluation tab)."""
    return (EvaluationAttachment.objects.filter(submission__registration__activity=activity)
            .select_related("submission__registration", "marked_by").order_by("uploaded_at", "attachment_id"))


def attachment_count(activity) -> int:
    return EvaluationAttachment.objects.filter(submission__registration__activity=activity).count()


def photos_for_reports(activities) -> QuerySet:
    """THE query for reports (Prompts 6–7): only files staff ticked "Use in reports", for the given activities (an
    Activity, a queryset or a list of IDs). Nothing else may put participant files into a report or send them to the AI."""
    if hasattr(activities, "pk"):
        activities = [activities.pk]
    return (EvaluationAttachment.objects.filter(use_in_reports=True, submission__registration__activity__in=activities)
            .select_related("submission__registration__activity").order_by("uploaded_at", "attachment_id"))


def set_use_in_reports(member, attachment: EvaluationAttachment, use: bool, now: datetime | None = None) -> EvaluationAttachment:
    """Tick or untick "Use in reports" (can_create_activity). Keeps who and when (the latest change) and logs it on
    registrations.audit with IDs only. Setting the value it already has changes nothing."""
    require_permission_for(member, VIEW_PERMISSION, attachment.submission.registration.activity)  # or assigned (460)
    with transaction.atomic():
        attachment = EvaluationAttachment.objects.select_for_update().get(pk=attachment.pk)
        if attachment.use_in_reports == bool(use):
            return attachment
        attachment.use_in_reports = bool(use)
        attachment.marked_by = member
        attachment.marked_at = now or timezone.now()
        attachment.save(update_fields=["use_in_reports", "marked_by", "marked_at"])
    audit_log.info("Evaluation photo %s %s in reports by member %s.", attachment.pk,
                   "marked for use" if use else "no longer used", member.pk)
    return attachment


def file_bytes(attachment: EvaluationAttachment) -> bytes | None:
    """The stored file, or None when it cannot be read."""
    try:
        with attachment.file_path.open("rb") as handle:
            return handle.read()
    except (FileNotFoundError, OSError, ValueError):
        return None


# --- Deleting --------------------------------------------------------------------------------------------------------


def remove_files(names: list[str]) -> None:
    """Remove stored files; a file already gone is fine. Never raises."""
    for name in names:
        try:
            default_storage.delete(name)
        except OSError as error:
            logger.warning("A participant photo could not be removed: %s.", type(error).__name__)


def remove_file_after_delete(sender, instance: EvaluationAttachment, **kwargs) -> None:
    """post_delete receiver: the row is gone (also through a cascade from the submission, the registration or the
    activity, item 389), so remove its file once the transaction commits (a rollback keeps the file)."""
    name = instance.file_path.name if instance.file_path else ""
    if name:
        transaction.on_commit(lambda: remove_files([name]))
