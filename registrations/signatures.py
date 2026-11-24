"""Participant signatures (ASSUMPTIONS 434): the one place that checks, stores, shows and deletes them.

A participant gives their signature ONCE per registration and it is reused on every generated Attendance and
Confirmation of Attendance form (registrations.attendance_forms). Three kinds, the participant chooses:
- draw: drawn on the screen (a PNG data URL from the canvas, static/js/signature_pad.js);
- upload: an image file (JPG or PNG, content checked like document uploads, at most PARTICIPANT_SIGNATURE_MAX_KB);
- typed: the full name typed, plus the tick "I confirm this is my signature" (printed as the typed name).
Images are always re-encoded by Pillow to a small PNG (metadata and anything hidden in the file dropped, size capped)
and stored under signatures/activity-<id>/<random>.png. They are never served from /media/: staff see them only
through registrations.page_views.signature_image (staff login), and the PDFs embed them as data: URIs.

Retention (owner decision 2026-11-24): purge_signatures() deletes them once the forms are kept as documents, or
PARTICIPANT_SIGNATURE_RETENTION_DAYS after the end date (see signatures_due()).
"""

import base64
import binascii
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import AttendanceDay, ParticipantSignature, Registration

logger = logging.getLogger("registrations")
audit_log = logging.getLogger("registrations.audit")

FIELD = "signature"  # the error key on every form
KINDS = tuple(ParticipantSignature.Kind.values)
STAFF_KINDS = (ParticipantSignature.Kind.DRAW, ParticipantSignature.Kind.TYPED)  # on a staff device (owner decision b)
UPLOAD_ROOT = "signatures"
TYPED_NAME_MAX = 150
MAX_SOURCE_PIXELS = 25_000_000  # refuse absurd images before decoding them (decompression bombs)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"

REQUIRED = "Give your signature: draw it, upload a picture of it, or type your full name."
CHOOSE_KIND = "Choose how to give your signature."
DRAW_FIRST = "Draw your signature in the box first."
DRAWN_TOO_LARGE = "The drawing is too large. Clear it and draw again."
UPLOAD_CHOOSE = "Choose a picture of your signature (JPG or PNG)."
UPLOAD_TYPE = "Only JPG and PNG pictures can be used as a signature."
UPLOAD_CONTENT = "This file is not a valid JPG or PNG picture."
TYPED_REQUIRED = "Type your full name."
TYPED_LETTERS = "Type your full name (letters)."
TYPED_CONFIRM = "Tick “I confirm this is my signature”."
DELETED_TEXT = "Signature deleted (kept only until the forms were generated)"


def upload_too_large() -> str:
    return f"The picture is larger than {settings.PARTICIPANT_SIGNATURE_MAX_KB} KB."


@dataclass(frozen=True)
class CleanSignature:
    """A checked signature, ready to save: a PNG (draw, upload) or a typed name."""

    kind: str
    png: bytes | None = None
    typed_name: str | None = None


# --- Reading what was sent -----------------------------------------------------------------------------------------


def from_post(post, files, prefix: str = FIELD) -> dict:
    """The signature fields of an HTML form (templates/public/_signature_field.html) as clean_signature takes them."""
    return {
        "kind": post.get(f"{prefix}_kind", ""),
        "drawn": post.get(f"{prefix}_drawn", ""),
        "file": files.get(f"{prefix}_file") if files is not None else None,
        "typed_name": post.get(f"{prefix}_typed_name", ""),
        "confirmed": post.get(f"{prefix}_confirm", "") in ("yes", "on", "true", "1"),
    }


def from_json(value) -> dict:
    """The JSON API's {"kind": "draw" | "upload" | "typed", "image": "data:image/png;base64,…", "typed_name": "…",
    "confirmed": true}. An uploaded picture is sent as a data URL too (JPG or PNG)."""
    value = value if isinstance(value, dict) else {}
    kind = str(value.get("kind") or "")
    image = str(value.get("image") or "")
    return {
        "kind": kind,
        "drawn": image if kind == ParticipantSignature.Kind.DRAW else "",
        "data_url": image if kind == ParticipantSignature.Kind.UPLOAD else "",
        "typed_name": str(value.get("typed_name") or ""),
        "confirmed": value.get("confirmed") is True,
    }


def is_given(raw: dict | None) -> bool:
    """Whether anything at all was sent (a kind chosen)."""
    return bool(raw and str(raw.get("kind") or "").strip())


# --- Checking --------------------------------------------------------------------------------------------------------


def _refuse(message: str):
    raise ValidationError({FIELD: [message]})


def clean_signature(raw: dict | None, kinds: tuple[str, ...] = KINDS) -> CleanSignature:
    """The one check of a signature. Raises ValidationError on FIELD with one plain message."""
    raw = raw or {}
    kind = str(raw.get("kind") or "").strip()
    if not kind:
        _refuse(REQUIRED)
    if kind not in kinds:
        _refuse(CHOOSE_KIND)
    if kind == ParticipantSignature.Kind.TYPED:
        return CleanSignature(kind, typed_name=_clean_typed(raw))
    if kind == ParticipantSignature.Kind.DRAW:
        content = _decode_data_url(str(raw.get("drawn") or ""), settings.PARTICIPANT_SIGNATURE_DRAWN_MAX_KB,
                                   empty=DRAW_FIRST, too_large=DRAWN_TOO_LARGE, png_only=True)
        return CleanSignature(kind, png=_reencode(content, drawn=True))
    content = _upload_bytes(raw)
    return CleanSignature(kind, png=_reencode(content, drawn=False))


def _clean_typed(raw: dict) -> str:
    name = " ".join(str(raw.get("typed_name") or "").split())
    if not name:
        _refuse(TYPED_REQUIRED)
    if len(name) > TYPED_NAME_MAX:
        _refuse(f"The name can be at most {TYPED_NAME_MAX} characters.")
    if not any(char.isalpha() for char in name):
        _refuse(TYPED_LETTERS)
    if raw.get("confirmed") is not True:
        _refuse(TYPED_CONFIRM)
    return name


def _decode_data_url(value: str, max_kb: int, *, empty: str, too_large: str, png_only: bool) -> bytes:
    value = value.strip()
    if not value:
        _refuse(empty)
    if len(value) > max_kb * 1024 * 4 // 3 + 100:  # base64 is 4/3 of the bytes: refuse before decoding
        _refuse(too_large)
    prefixes = ("data:image/png;base64,",) if png_only else ("data:image/png;base64,", "data:image/jpeg;base64,")
    prefix = next((p for p in prefixes if value.startswith(p)), None)
    if prefix is None:
        _refuse(UPLOAD_CONTENT if not png_only else DRAW_FIRST)
    try:
        content = base64.b64decode(value[len(prefix):], validate=True)
    except (binascii.Error, ValueError):
        _refuse(UPLOAD_CONTENT if not png_only else DRAW_FIRST)
    if len(content) > max_kb * 1024:
        _refuse(too_large)
    return content


def _upload_bytes(raw: dict) -> bytes:
    """An uploaded picture: a file (HTML form) or a data URL (JSON API). JPG/PNG by name and by real content."""
    if raw.get("data_url"):
        content = _decode_data_url(str(raw["data_url"]), settings.PARTICIPANT_SIGNATURE_MAX_KB,
                                   empty=UPLOAD_CHOOSE, too_large=upload_too_large(), png_only=False)
    else:
        file = raw.get("file")
        if file is None or not getattr(file, "size", 0):
            _refuse(UPLOAD_CHOOSE)
        name = (getattr(file, "name", "") or "").lower()
        if not name.endswith((".jpg", ".jpeg", ".png")):
            _refuse(UPLOAD_TYPE)
        if file.size > settings.PARTICIPANT_SIGNATURE_MAX_KB * 1024:
            _refuse(upload_too_large())
        file.seek(0)
        content = file.read()
        file.seek(0)
    if not (content.startswith(PNG_SIGNATURE) or content.startswith(JPEG_SIGNATURE)):
        _refuse(UPLOAD_CONTENT)
    return content


def _reencode(content: bytes, *, drawn: bool) -> bytes:
    """Open with Pillow (only PNG / JPEG), refuse an empty drawing, and save a fresh PNG that fits
    PARTICIPANT_SIGNATURE_MAX_PIXELS. Nothing of the original file (metadata, extra chunks) is kept."""
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in ("PNG", "JPEG"):
                _refuse(UPLOAD_CONTENT)
            width, height = source.size
            if width * height > MAX_SOURCE_PIXELS or not width or not height:
                _refuse(UPLOAD_CONTENT)
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError, ValueError):
        _refuse(UPLOAD_CONTENT if not drawn else DRAW_FIRST)
    if drawn and image.getchannel("A").getbbox() is None:  # a blank canvas: nothing drawn
        _refuse(DRAW_FIRST)
    image.thumbnail(settings.PARTICIPANT_SIGNATURE_MAX_PIXELS)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# --- Saving and reading ----------------------------------------------------------------------------------------------


def usable(registration: Registration) -> ParticipantSignature | None:
    """The registration's signature when it can still be printed (given and not deleted), else None."""
    try:
        signature = registration.signature
    except ParticipantSignature.DoesNotExist:
        return None
    return None if signature.is_deleted else signature


def has_signature(registration: Registration) -> bool:
    return usable(registration) is not None


def save_signature(registration: Registration, clean: CleanSignature, now: datetime | None = None) -> ParticipantSignature:
    """Store a checked signature for a registration that has none (or only a deleted one). Never replaces a usable
    signature: it is given once (the caller checks has_signature first). The file is written first; if saving the row
    fails, the file is removed again."""
    now = now or timezone.now()
    try:
        signature = registration.signature
    except ParticipantSignature.DoesNotExist:
        signature = ParticipantSignature(registration=registration)
    signature.kind, signature.signed_at, signature.deleted_at = clean.kind, now, None
    signature.typed_name = clean.typed_name
    signature.file_path = ""
    stored = ""
    if clean.png is not None:
        name = f"{UPLOAD_ROOT}/activity-{registration.activity_id}/{uuid.uuid4().hex}.png"
        stored = default_storage.save(name, ContentFile(clean.png))
        signature.file_path.name = stored
    try:
        with transaction.atomic():
            signature.save()
    except Exception:
        if stored:
            default_storage.delete(stored)
        raise
    registration.signature = signature
    audit_log.info("Signature saved (%s): registration %s, activity %s.", clean.kind, registration.pk, registration.activity_id)
    return signature


def image_bytes(signature: ParticipantSignature) -> bytes | None:
    """The stored PNG, or None (typed, deleted, or the file cannot be read)."""
    if signature.is_deleted or not signature.file_path:
        return None
    try:
        with signature.file_path.open("rb") as handle:
            return handle.read()
    except (FileNotFoundError, OSError, ValueError):
        return None


def data_uri(signature: ParticipantSignature) -> str | None:
    content = image_bytes(signature)
    return f"data:image/png;base64,{base64.b64encode(content).decode('ascii')}" if content else None


def label(registration: Registration) -> str:
    """Short text for staff lists: how the signature was given, or that there is none."""
    try:
        signature = registration.signature
    except ParticipantSignature.DoesNotExist:
        return "No signature"
    return "Deleted (retention)" if signature.is_deleted else signature.get_kind_display()


# --- Retention (owner decision 2026-11-24) ---------------------------------------------------------------------------

RETENTION_DOCUMENT_TYPES = ("Attendance Sheet", settings.CONFIRMATION_OF_ATTENDANCE, "Activity Report")
FORM_DOCUMENT_TYPES = ("Attendance Sheet", settings.CONFIRMATION_OF_ATTENDANCE)


def last_change(activity) -> datetime | None:
    """The latest change that a generated form would show: a registration changed (registered, cancelled, restored,
    attendance marked or undone: every one of them saves the registration), an attendance day recorded, or a signature
    given. registrations.attendance touches the registration on every attendance change."""
    registrations = Registration.objects.filter(activity=activity)
    moments = [
        registrations.aggregate(at=Max("updated_at"))["at"],
        AttendanceDay.objects.filter(registration__activity=activity).aggregate(at=Max("created_at"))["at"],
        ParticipantSignature.objects.filter(registration__activity=activity).aggregate(at=Max("signed_at"))["at"],
    ]
    moments = [moment for moment in moments if moment is not None]
    return max(moments) if moments else None


def forms_are_kept(activity) -> bool:
    """The early trigger: the activity is Conducted, an Approved Attendance Sheet, Confirmation of Attendance and
    Activity Report are all uploaded (approved copies, item 432; generated or scanned), and the newest Attendance Sheet
    and Confirmation of Attendance are newer than the last change (so the official records show everyone)."""
    from activities.models import ActivityStatus
    from documents.models import Document, DocumentStatus

    if activity.status.name != ActivityStatus.CONDUCTED:
        return False
    approved = Document.objects.activity_documents().filter(
        activity=activity, status__status_name=DocumentStatus.APPROVED)
    newest = dict(approved.filter(document_type__name__in=RETENTION_DOCUMENT_TYPES).values_list(
        "document_type__name").annotate(at=Max("updated_at")))
    if any(name not in newest for name in RETENTION_DOCUMENT_TYPES):
        return False
    changed = last_change(activity)
    return changed is None or all(newest[name] > changed for name in FORM_DOCUMENT_TYPES)


def retention_ends(activity) -> datetime:
    """The latest moment the signatures are kept: 00:00 Manila, PARTICIPANT_SIGNATURE_RETENTION_DAYS after the end date."""
    day = activity.end_date + timedelta(days=settings.PARTICIPANT_SIGNATURE_RETENTION_DAYS)
    return timezone.make_aware(datetime.combine(day, datetime.min.time()))


def signatures_due(activity, now: datetime | None = None) -> str | None:
    """Why this activity's signatures are deleted now ("forms kept" / "one year"), or None to keep them."""
    now = now or timezone.now()
    if now >= retention_ends(activity):
        return "one year"
    if forms_are_kept(activity):
        return "forms kept"
    return None


def purge_signatures(now: datetime | None = None) -> dict[str, int]:
    """The daily job (and `manage.py purge_participant_signatures`): delete the signatures of every activity that is
    due (signatures_due). The image file is removed after the commit, typed_name cleared, deleted_at set; the row and
    the generated PDFs stay (the PDFs are the official records). Returns {"activities": n, "signatures": n}."""
    from activities.models import Activity

    now = now or timezone.now()
    kept = ParticipantSignature.objects.filter(deleted_at__isnull=True)
    activity_ids = kept.values_list("registration__activity_id", flat=True).distinct()
    counts = {"activities": 0, "signatures": 0}
    for activity in Activity.objects.filter(pk__in=list(activity_ids)).select_related("status").order_by("pk"):
        reason = signatures_due(activity, now)
        if reason is None:
            continue
        counts["activities"] += 1
        counts["signatures"] += delete_signatures(activity, now)
        logger.info("Signatures of activity %s deleted (%s).", activity.pk, reason)
    return counts


@transaction.atomic
def delete_signatures(activity, now: datetime | None = None) -> int:
    """Delete every kept signature of one activity (retention). Files are removed only after the commit."""
    now = now or timezone.now()
    rows = ParticipantSignature.objects.select_for_update().filter(registration__activity=activity, deleted_at__isnull=True)
    names = [name for name in rows.values_list("file_path", flat=True) if name]
    count = rows.update(deleted_at=now, typed_name=None, file_path="")
    transaction.on_commit(lambda: remove_files(names))
    return count


def remove_files(names: list[str]) -> None:
    """Remove stored signature files; a file already gone is fine. Never raises."""
    for name in names:
        try:
            default_storage.delete(name)
        except OSError as error:
            logger.warning("A signature file could not be removed: %s.", type(error).__name__)


def deleted_signature_q() -> Q:
    """Registrations whose signature was deleted by the retention job."""
    return Q(signature__deleted_at__isnull=False)
