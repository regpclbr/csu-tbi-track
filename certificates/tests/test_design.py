"""Templates and signatories (Admin): image checks, activation, storage, never public."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from certificates import services
from certificates.models import CertificateSignatory, CertificateTemplate
from conftest import image_bytes

from .conftest import A4, template_upload, upload

pytestmark = pytest.mark.django_db


# --- Templates ---------------------------------------------------------------------------------


def test_upload_template_and_activate(admin_member) -> None:
    first = services.upload_template(admin_member, " TBI 2026 ", template_upload(), activate=True)
    assert first.is_active and first.name == "TBI 2026" and first.uploaded_by == admin_member
    second = services.upload_template(admin_member, "TBI 2027", template_upload("b.jpg", kind="JPEG"), activate=True)
    first.refresh_from_db()
    assert second.is_active and not first.is_active  # exactly one active
    third = services.upload_template(admin_member, "Draft design", template_upload())
    assert not third.is_active


def test_stored_name_never_uses_the_uploaded_name(admin_member) -> None:
    template = services.upload_template(admin_member, "Design", template_upload("..\\..\\evil name.png"))
    assert template.file_path.name.startswith("certificate_templates/template-")
    assert "evil" not in template.file_path.name and template.file_path.name.endswith(".png")


@pytest.mark.parametrize(
    "file, message",
    [
        (upload("design.gif", image_bytes(*A4)), "Only JPG or PNG"),
        (upload("design.png", b""), "empty"),
        (upload("design.png", b"%PDF-1.7 not an image"), "not a valid PNG"),
        (upload("design.jpg", image_bytes(*A4)), "not a valid JPG"),  # a PNG renamed to .jpg
        (upload("design.png", image_bytes(1754, 1754)), "A4 landscape"),  # square
        (upload("design.png", image_bytes(1240, 1754)), "A4 landscape"),  # portrait
        (upload("design.png", image_bytes(1000, 707)), "at least 1754 x 1240"),  # A4 but too small
        (upload("design.png", image_bytes(*A4)[:200]), "not a valid PNG"),  # cut off / damaged
    ],
)
def test_template_image_checks(admin_member, file, message) -> None:
    with pytest.raises(ValidationError, match=message):
        services.upload_template(admin_member, "Design", file)
    assert not CertificateTemplate.objects.exists()


def test_template_image_size_limit(admin_member, settings) -> None:
    settings.CERTIFICATE_IMAGE_MAX_MB = 0.001
    with pytest.raises(ValidationError, match="larger than"):
        services.upload_template(admin_member, "Design", template_upload())


def test_template_needs_a_name(admin_member) -> None:
    with pytest.raises(ValidationError):
        services.upload_template(admin_member, "  ", template_upload())


def test_only_admin_manages_templates(director) -> None:
    with pytest.raises(PermissionDenied):
        services.upload_template(director, "Design", template_upload())


def test_deactivate_template(admin_member) -> None:
    template = services.upload_template(admin_member, "Design", template_upload(), activate=True)
    assert not services.deactivate_template(admin_member, template).is_active


# --- Signatories ----------------------------------------------------------------------------


def test_new_signatory_replaces_the_active_one_for_that_position(admin_member) -> None:
    old = services.add_signatory(admin_member, "Old VP", "vp", upload("sig.png", image_bytes()))
    director = services.add_signatory(admin_member, "Director", "director", upload("d.png", image_bytes()))
    new = services.add_signatory(admin_member, "New VP", "vp", upload("sig2.png", image_bytes()))
    old.refresh_from_db()
    director.refresh_from_db()
    assert new.is_active and not old.is_active and director.is_active
    assert new.signature_image_path.name.startswith("signatures/vp-")


@pytest.mark.parametrize(
    "file, message",
    [
        (upload("sig.jpg", image_bytes(kind="JPEG")), "Only PNG"),
        (upload("sig.png", image_bytes(kind="JPEG")), "not a valid PNG"),
        (upload("sig.png", b"GIF89a"), "not a valid PNG"),
    ],
)
def test_signature_must_be_a_real_png(admin_member, file, message) -> None:
    with pytest.raises(ValidationError, match=message):
        services.add_signatory(admin_member, "VP", "vp", file)
    assert not CertificateSignatory.objects.exists()


def test_signatory_position_must_be_vp_or_director(admin_member) -> None:
    with pytest.raises(ValidationError, match="vp or director"):
        services.add_signatory(admin_member, "Dean", "dean", upload("s.png", image_bytes()))


def test_only_admin_manages_signatories(director) -> None:
    with pytest.raises(PermissionDenied):
        services.add_signatory(director, "VP", "vp", upload("s.png", image_bytes()))


# --- Images are served only through permission-checked views (item 65) -------------------------


@pytest.fixture
def stored(admin_member):
    template = services.upload_template(admin_member, "Design", template_upload(), activate=True)
    signatory = services.add_signatory(admin_member, "VP", "vp", upload("sig.png", image_bytes()))
    return template, signatory


def test_admin_downloads_images_as_attachments(client, admin_member, stored) -> None:
    template, signatory = stored
    client.force_login(admin_member)
    for url in (reverse("certificates:template_image", args=[template.pk]), reverse("certificates:signature_image", args=[signatory.pk])):
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("attachment")
        assert response["X-Content-Type-Options"] == "nosniff"


def test_signature_images_are_never_public(client, director, stored, startup_portal) -> None:
    template, signatory = stored
    urls = [reverse("certificates:signature_image", args=[signatory.pk]), reverse("certificates:template_image", args=[template.pk])]
    for url in urls:
        assert client.get(url).status_code == 302  # not logged in: sent to the staff login
    client.force_login(director)  # staff without can_manage_users
    for url in urls:
        assert client.get(url).status_code == 403
    for url in urls:  # a logged-in startup is never staff
        assert startup_portal.get(url).status_code == 302
    # Never under /media/ (not even in DEBUG).
    assert client.get("/media/" + signatory.signature_image_path.name).status_code == 404


def test_admin_console_api(client, admin_member, director) -> None:
    client.force_login(admin_member)
    response = client.post(
        reverse("certificates:api_signatories"), {"name": "VP", "position": "vp", "file": upload("s.png", image_bytes())}
    )
    assert response.status_code == 201 and response.json()["signatory"]["is_active"]
    response = client.post(reverse("certificates:api_templates"), {"name": "Design", "file": template_upload(), "activate": "true"})
    assert response.status_code == 201
    template_id = response.json()["template"]["certificate_template_id"]
    assert client.post(reverse("certificates:api_template_action", args=[template_id, "deactivate"])).json()["template"]["is_active"] is False
    bad = client.post(reverse("certificates:api_templates"), {"name": "Design", "file": upload("x.png", b"nope")})
    assert bad.status_code == 400 and "file" in bad.json()["errors"]
    client.force_login(director)
    assert client.get(reverse("certificates:api_templates")).status_code == 403


# --- Django admin ---------------------------------------------------------------------------------


def test_django_admin_adds_signatory_through_the_service(client, admin_member) -> None:
    client.force_login(admin_member)
    services.add_signatory(admin_member, "Old VP", "vp", upload("s.png", image_bytes()))
    response = client.post(
        "/admin/certificates/certificatesignatory/add/",
        {"name": "New VP", "position": "vp", "signature_image_path": upload("n.png", image_bytes())},
    )
    assert response.status_code == 302, response.content[:500]
    assert list(CertificateSignatory.objects.filter(is_active=True).values_list("name", flat=True)) == ["New VP"]
    bad = client.post(
        "/admin/certificates/certificatesignatory/add/",
        {"name": "X", "position": "vp", "signature_image_path": upload("n.png", image_bytes(kind="JPEG"))},
    )
    assert bad.status_code == 200 and b"not a valid PNG" in bad.content


def test_django_admin_adds_template_through_the_service(client, admin_member) -> None:
    client.force_login(admin_member)
    response = client.post(
        "/admin/certificates/certificatetemplate/add/", {"name": "Design", "file_path": template_upload(), "activate": "on"}
    )
    assert response.status_code == 302, response.content[:500]
    assert CertificateTemplate.objects.get().is_active


def test_django_admin_never_deletes_and_certificates_are_view_only(client, admin_member, stored) -> None:
    from django.contrib import admin

    from certificates.models import Certificate

    client.force_login(admin_member)
    request = type("R", (), {"user": admin_member})()
    for model in (CertificateTemplate, CertificateSignatory, Certificate):
        assert not admin.site._registry[model].has_delete_permission(request)
    certificate_admin = admin.site._registry[Certificate]
    assert not certificate_admin.has_add_permission(request) and not certificate_admin.has_change_permission(request)


def test_sample_images_pass_the_upload_checks(tmp_path) -> None:
    from django.core.management import call_command

    call_command("make_sample_certificate_images", str(tmp_path))
    assert services.validate_template_image(upload("t.png", (tmp_path / "sample-template.png").read_bytes())) == "png"
    for position in ("vp", "director"):
        signature = upload("s.png", (tmp_path / f"sample-signature-{position}.png").read_bytes())
        assert services.validate_signature_image(signature) == "png"
