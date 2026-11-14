"""Database constraints on certificates, templates and signatories."""

import pytest
from django.db import IntegrityError

from certificates.models import Certificate, CertificateSignatory, CertificateTemplate


@pytest.mark.django_db
def test_new_certificate_starts_as_generated(registration, make_certificate) -> None:
    assert make_certificate(registration).status == Certificate.Status.GENERATED


@pytest.mark.django_db
def test_one_participation_certificate_per_registration(registration, make_certificate) -> None:
    make_certificate(registration, "TBI-0001")
    with pytest.raises(IntegrityError):
        make_certificate(registration, "TBI-0002")


@pytest.mark.django_db
def test_certificate_number_is_unique(registration, make_certificate, activity, registration_form) -> None:
    from registrations.models import Registration

    other = Registration.objects.create(
        activity=activity,
        template=registration_form,
        participant_category=activity.participant_categories.first(),
        name="Ben",
        email="b@x.com",
    )
    make_certificate(registration, "TBI-0001")
    with pytest.raises(IntegrityError):
        make_certificate(other, "TBI-0001")


@pytest.mark.django_db
def test_one_active_certificate_template(member, certificate_template) -> None:
    CertificateTemplate.objects.create(name="Old", file_path="t/old.pdf", uploaded_by=member, is_active=False)
    with pytest.raises(IntegrityError):
        CertificateTemplate.objects.create(name="Second", file_path="t/b.pdf", uploaded_by=member, is_active=True)


@pytest.mark.django_db
def test_one_default_signatory_per_slot(signatories) -> None:
    """ASSUMPTIONS 363: many people may share a position; only one default per side."""
    CertificateSignatory.objects.create(name="Another VP", position="Vice President", signature_image_path="s.png")
    with pytest.raises(IntegrityError):
        CertificateSignatory.objects.create(name="New VP", position="Vice President", default_slot="right", signature_image_path="s.png")


def test_file_paths_allow_255_characters() -> None:
    assert Certificate._meta.get_field("file_path").max_length == 255
    assert CertificateTemplate._meta.get_field("file_path").max_length == 255
    assert CertificateSignatory._meta.get_field("signature_image_path").max_length == 255
