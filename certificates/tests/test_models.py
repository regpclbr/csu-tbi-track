"""Database constraints on certificates, templates and signatories."""

import pytest
from django.db import IntegrityError

from certificates.models import Certificate, CertificateSignatory, CertificateTemplate


@pytest.fixture
def signatories() -> tuple[CertificateSignatory, CertificateSignatory]:
    vp = CertificateSignatory.objects.create(name="VP", position="vp", signature_image_path="signatures/vp.png")
    director = CertificateSignatory.objects.create(
        name="Director", position="director", signature_image_path="signatures/d.png"
    )
    return vp, director


def make_certificate(registration, signatories, number: str) -> Certificate:
    vp, director = signatories
    return Certificate.objects.create(
        certificate_number=number,
        recipient_type="registration",
        registration=registration,
        activity=registration.activity,
        vp_signatory=vp,
        director_signatory=director,
        delivery_method="email",
    )


@pytest.mark.django_db
def test_one_participation_certificate_per_registration(registration, signatories) -> None:
    make_certificate(registration, signatories, "TBI-0001")
    with pytest.raises(IntegrityError):
        make_certificate(registration, signatories, "TBI-0002")


@pytest.mark.django_db
def test_one_active_certificate_template(member) -> None:
    CertificateTemplate.objects.create(name="Old", file_path="t/old.pdf", uploaded_by=member, is_active=False)
    CertificateTemplate.objects.create(name="Current", file_path="t/a.pdf", uploaded_by=member, is_active=True)
    with pytest.raises(IntegrityError):
        CertificateTemplate.objects.create(name="Second", file_path="t/b.pdf", uploaded_by=member, is_active=True)


@pytest.mark.django_db
def test_one_active_signatory_per_position(signatories) -> None:
    CertificateSignatory.objects.create(name="Old VP", position="vp", signature_image_path="s.png", is_active=False)
    with pytest.raises(IntegrityError):
        CertificateSignatory.objects.create(name="New VP", position="vp", signature_image_path="s.png")
