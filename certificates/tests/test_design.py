"""Templates (Admin) and the signatory list (can_approve_certificates, ASSUMPTIONS 363): image checks, activation,
storage, never public."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from certificates import services
from certificates.models import CertificateSignatory, CertificateTemplate
from conftest import image_bytes
from conftest import VIEW_ONLY_ROLE

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


def test_signatories_are_a_list_of_people_with_free_text_positions(director) -> None:
    president = services.add_signatory(director, " Dr. Ana Reyes ", " University President ", upload("p.png", image_bytes()))
    vp = services.add_signatory(director, "Dr. Ben Cruz", "Vice President for Research", None)
    third = services.add_signatory(director, "Engr. Cy Lim", "Director, NAVIGATU", upload("d.jpg", image_bytes(kind="JPEG")))
    assert (president.name, president.position) == ("Dr. Ana Reyes", "University President")
    assert all(person.is_active for person in (president, vp, third))  # no "one active per position" any more
    assert not vp.signature_image_path
    assert president.signature_image_path.name.startswith("signatures/signature-")  # never the position or the upload
    assert third.signature_image_path.name.endswith(".png")  # a JPG is stored as a transparent PNG (ASSUMPTIONS 366)


@pytest.mark.parametrize(
    "file, message",
    [
        (upload("sig.gif", b"GIF89a"), "Only JPG or PNG"),
        (upload("sig.png", image_bytes(kind="JPEG")), "not a valid PNG"),
        (upload("sig.jpg", image_bytes()), "not a valid JPG"),
        (upload("sig.png", b"GIF89a"), "not a valid PNG"),
    ],
)
def test_signature_must_be_a_real_png_or_jpg(director, file, message) -> None:
    with pytest.raises(ValidationError, match=message):
        services.add_signatory(director, "VP", "Vice President", file)
    assert not CertificateSignatory.objects.exists()


def test_signatory_needs_a_name_and_a_position(director) -> None:
    with pytest.raises(ValidationError) as error:
        services.add_signatory(director, "  ", "", None)
    assert set(error.value.message_dict) == {"name", "position"}


@pytest.mark.parametrize("role_name", [VIEW_ONLY_ROLE, "Incubation Project Head"])  # an approver too (ASSUMPTIONS 418)
def test_only_signatory_managers_manage_signatories(make_member, director, role_name) -> None:
    staff = make_member(role_name, email="staff.sig@example.com")
    person = services.add_signatory(director, "VP", "Vice President", None)
    other = services.add_signatory(director, "P", "President", None)
    for call in (
        lambda: services.add_signatory(staff, "VP", "Vice President", None),
        lambda: services.edit_signatory(staff, person, "X", "Y"),
        lambda: services.replace_signature(staff, person, upload("s.png", image_bytes())),
        lambda: services.deactivate_signatory(staff, person),
        lambda: services.set_default_signatories(staff, person, other),
    ):
        with pytest.raises(PermissionDenied):
            call()


def test_defaults_two_different_active_people(director) -> None:
    a = services.add_signatory(director, "A", "Director", None)
    b = services.add_signatory(director, "B", "President", None)
    c = services.add_signatory(director, "C", "VP", None)
    services.set_default_signatories(director, a, b)
    assert services.default_signatories() == {"left": a, "right": b}
    services.set_default_signatories(director, b, c)  # swapping sides works in one step
    assert services.default_signatories() == {"left": b, "right": c}
    a.refresh_from_db()
    assert a.default_slot is None
    with pytest.raises(services.CertificateError, match="both sides"):
        services.set_default_signatories(director, c, c)
    services.deactivate_signatory(director, a)
    with pytest.raises(services.CertificateError, match="inactive"):
        services.set_default_signatories(director, a, c)


def test_an_active_default_cannot_be_deactivated(director) -> None:
    a = services.add_signatory(director, "A", "Director", None)
    b = services.add_signatory(director, "B", "President", None)
    services.set_default_signatories(director, a, b)
    with pytest.raises(services.CertificateError, match="Choose another default first"):
        services.deactivate_signatory(director, a)
    c = services.add_signatory(director, "C", "VP", None)
    services.set_default_signatories(director, c, b)
    assert not services.deactivate_signatory(director, a).is_active


# --- Images are served only through permission-checked views (item 65) -------------------------


@pytest.fixture
def stored(admin_member, director):
    template = services.upload_template(admin_member, "Design", template_upload(), activate=True)
    signatory = services.add_signatory(director, "VP", "Vice President", upload("sig.png", image_bytes()))  # ASSUMPTIONS 418
    return template, signatory


def test_admin_downloads_images_as_attachments(client, admin_member, stored) -> None:
    template, signatory = stored
    client.force_login(admin_member)
    for url in (reverse("certificates:template_image", args=[template.pk]), reverse("certificates:signature_image", args=[signatory.pk])):
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("attachment")
        assert response["X-Content-Type-Options"] == "nosniff"


def test_signature_images_are_never_public(client, director, make_member, stored) -> None:
    template, signatory = stored
    urls = [reverse("certificates:signature_image", args=[signatory.pk]), reverse("certificates:template_image", args=[template.pk])]
    for url in urls:
        assert client.get(url).status_code == 302  # not logged in: sent to the staff login
    client.force_login(make_member(VIEW_ONLY_ROLE))  # staff with neither can_manage_users nor can_approve_certificates
    for url in urls:
        assert client.get(url).status_code == 403
    client.force_login(director)  # approves certificates: the signature (Certificates tab), never the template
    assert client.get(urls[0]).status_code == 200 and client.get(urls[1]).status_code == 403
    # Never under /media/ (not even in DEBUG).
    assert client.get("/media/" + signatory.signature_image_path.name).status_code == 404


def test_admin_console_api(client, admin_member, director, make_member) -> None:
    client.force_login(admin_member)
    post = {"name": "VP", "position": "Vice President", "file": upload("s.png", image_bytes())}
    assert client.post(reverse("certificates:api_signatories"), post).status_code == 403  # the Director's (ASSUMPTIONS 418)
    response = client.post(reverse("certificates:api_templates"), {"name": "Design", "file": template_upload(), "activate": "true"})
    assert response.status_code == 201
    template_id = response.json()["template"]["certificate_template_id"]
    assert client.post(reverse("certificates:api_template_action", args=[template_id, "deactivate"])).json()["template"]["is_active"] is False
    bad = client.post(reverse("certificates:api_templates"), {"name": "Design", "file": upload("x.png", b"nope")})
    assert bad.status_code == 400 and "file" in bad.json()["errors"]
    client.force_login(director)
    assert client.get(reverse("certificates:api_templates")).status_code == 403
    assert client.get(reverse("certificates:api_signatories")).status_code == 200  # can_manage_signatories (ASSUMPTIONS 418)
    response = client.post(
        reverse("certificates:api_signatories"), {"name": "VP", "position": "Vice President", "file": upload("s.png", image_bytes())}
    )
    assert response.status_code == 201 and response.json()["signatory"]["is_active"]
    assert response.json()["signatory"]["position"] == "Vice President"
    client.force_login(make_member("Admin Staff", email="staff.approver@example.com"))  # approves certificates, not this
    assert client.get(reverse("certificates:api_signatories")).status_code == 403
    client.force_login(make_member(VIEW_ONLY_ROLE, email="staff.api@example.com"))
    assert client.get(reverse("certificates:api_signatories")).status_code == 403


# --- Django admin ---------------------------------------------------------------------------------


def test_django_admin_signatories_are_view_only(client, admin_member) -> None:
    from django.contrib import admin

    from certificates.models import ActivitySignatory

    request = type("R", (), {"user": admin_member})()
    for model in (CertificateSignatory, ActivitySignatory):
        model_admin = admin.site._registry[model]
        assert not model_admin.has_add_permission(request) and not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)


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
