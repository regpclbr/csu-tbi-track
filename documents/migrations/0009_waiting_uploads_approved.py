"""Data only (ASSUMPTIONS 432, NAVIGATU: only approved paper copies are uploaded). Activity documents still "Waiting for
review" (status For Review, reviewed_at empty) of a type that does not need review become Approved by System
(reviewed_by empty, reviewed_at = the time of this migration), as if they had been uploaded as approved copies.

Guarded: certificate evidence (related_certificate set) and documents of a type with needs_review switched on are never
touched; "Returned" documents (For Review, reviewed_at set) are left as they are (the uploader replaces them).
Nothing is indexed here: after `migrate`, run `python manage.py index_documents` (or wait for the 7:30 schedule).
Reverse: Approved documents with reviewed_by empty of a type that does not need review go back to waiting for review
(note: that also includes documents uploaded as approved copies after this migration).
"""

from django.db import migrations
from django.utils import timezone

FOR_REVIEW, APPROVED = "For Review", "Approved"


def _scope(apps):
    Document = apps.get_model("documents", "Document")
    return Document.objects.filter(related_certificate__isnull=True, document_type__needs_review=False)


def approve_waiting(apps, schema_editor):
    DocumentStatus = apps.get_model("documents", "DocumentStatus")
    approved = DocumentStatus.objects.filter(status_name=APPROVED).first()
    if approved is None:  # an empty database: nothing to do
        return
    _scope(apps).filter(status__status_name=FOR_REVIEW, reviewed_at__isnull=True).update(
        status=approved, reviewed_by=None, reviewed_at=timezone.now())


def back_to_waiting(apps, schema_editor):
    DocumentStatus = apps.get_model("documents", "DocumentStatus")
    for_review = DocumentStatus.objects.filter(status_name=FOR_REVIEW).first()
    if for_review is None:
        return
    _scope(apps).filter(status__status_name=APPROVED, reviewed_by__isnull=True).update(
        status=for_review, reviewed_at=None)


class Migration(migrations.Migration):
    dependencies = [("documents", "0008_documenttype_needs_review")]

    operations = [migrations.RunPython(approve_waiting, back_to_waiting)]
