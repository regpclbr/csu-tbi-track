"""Document queryset helpers: certificate files stay out of the checklist and repository."""

import pytest

from documents.models import Document, DocumentStatus, DocumentType


@pytest.mark.django_db
def test_certificate_documents_are_excluded_from_activity_documents(
    activity, member, registration, make_certificate
) -> None:
    status = DocumentStatus.objects.create(status_name=DocumentStatus.APPROVED)
    doc_type = DocumentType.objects.create(name="Certificate")
    certificate = make_certificate(registration)

    def make(title: str, related_certificate=None) -> Document:
        return Document.objects.create(
            activity=activity,
            document_type=doc_type,
            uploaded_by=member,
            status=status,
            related_certificate=related_certificate,
            title=title,
            file_name=f"{title}.pdf",
            file_path=f"documents/{title}.pdf",
            file_extension="pdf",
            file_size_kb=10,
        )

    report = make("report")
    cert_file = make("cert", related_certificate=certificate)

    assert list(Document.objects.activity_documents()) == [report]
    assert list(Document.objects.certificate_documents()) == [cert_file]
    assert set(Document.objects.approved()) == {report, cert_file}


def test_document_file_path_allows_255_characters() -> None:
    assert Document._meta.get_field("file_path").max_length == 255
