"""White backgrounds on signatures (ASSUMPTIONS 366): made transparent on upload (add or replace), a transparent PNG is
stored unchanged, "Keep the background", the size limit, the preview before saving, and "Remove white background" for
an existing image (a new version)."""

from io import BytesIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image, ImageDraw

from certificates import imaging, services
from certificates.models import CertificateSignatory

from .conftest import upload

pytestmark = pytest.mark.django_db


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def scanned_signature(kind: str = "JPEG") -> bytes:
    """A signature on white paper: dark blue ink, a little grey noise near white, white everywhere else."""
    image = Image.new("RGB", (200, 80), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.line([(10, 40), (190, 45)], fill=(20, 30, 120), width=6)
    draw.point([(5, 5), (6, 6)], fill=(250, 250, 248))
    buffer = BytesIO()
    image.save(buffer, format=kind, quality=95) if kind == "JPEG" else image.save(buffer, format=kind)
    return buffer.getvalue()


def transparent_png() -> bytes:
    image = Image.new("RGBA", (60, 30), (0, 0, 0, 0))
    ImageDraw.Draw(image).line([(5, 15), (55, 15)], fill=(0, 0, 0, 255), width=3)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def stored(signatory: CertificateSignatory) -> Image.Image:
    with signatory.signature_image_path.open("rb") as handle:
        image = Image.open(handle)
        image.load()
    return image


def test_a_white_background_becomes_transparent_and_the_ink_is_kept(director) -> None:
    person = services.add_signatory(director, "Dr. Alexander Bennett", "University President",
                                    upload("bennett.jpg", scanned_signature()))
    assert person.signature_image_path.name.endswith(".png")
    image = stored(person)
    assert image.mode == "RGBA" and image.format == "PNG"
    assert image.getpixel((150, 5))[3] == 0  # the paper is transparent
    assert image.getpixel((5, 5))[3] == 0  # near-white noise too
    red, green, blue, alpha = image.getpixel((100, 42))
    assert alpha == 255 and blue > red  # the ink stays opaque and blue


def test_a_transparent_png_is_stored_unchanged(director) -> None:
    content = transparent_png()
    person = services.add_signatory(director, "Dr. Clear", "Dean", upload("clear.png", content))
    with person.signature_image_path.open("rb") as handle:
        assert handle.read() == content


def test_keep_the_background_stores_the_image_as_uploaded(director) -> None:
    content = scanned_signature()
    person = services.add_signatory(director, "Dr. Keep", "Dean", upload("keep.jpg", content), keep_background=True)
    assert person.signature_image_path.name.endswith(".jpg")
    with person.signature_image_path.open("rb") as handle:
        assert handle.read() == content


@override_settings(CERTIFICATE_IMAGE_MAX_MB=0.001)  # about 1 KB
def test_the_size_limit_still_applies(director) -> None:
    with pytest.raises(ValidationError, match="larger than"):
        services.add_signatory(director, "Dr. Big", "Dean", upload("big.jpg", scanned_signature("PNG") * 3))
    assert not CertificateSignatory.objects.filter(name="Dr. Big").exists()


def test_replacing_an_image_also_removes_the_white_background(generated, director) -> None:
    activity, _ = generated
    result = services.upload_signature(director, activity, "right", upload("new.jpg", scanned_signature()))
    image = stored(result["signatory"])
    assert result["action"] == "replaced" and image.getpixel((150, 5))[3] == 0


def test_the_preview_shows_the_result_and_saves_nothing(director) -> None:
    before = CertificateSignatory.objects.count()
    response = client_for(director).post(reverse("certificates:signature_preview"),
                                         {"file": upload("scan.jpg", scanned_signature())})
    html = response.content.decode()
    assert response.status_code == 200 and "data:image/png;base64," in html
    assert "The white background was made transparent." in html and "repeating-conic-gradient" in html
    kept = client_for(director).post(reverse("certificates:signature_preview"),
                                     {"file": upload("scan.jpg", scanned_signature()), "keep_background": "true"})
    assert "data:image/jpeg;base64," in kept.content.decode() and "The background is kept" in kept.content.decode()
    assert CertificateSignatory.objects.count() == before


def test_the_preview_shows_the_refusal(director) -> None:
    html = client_for(director).post(reverse("certificates:signature_preview"),
                                     {"file": upload("note.png", b"not an image")}).content.decode()
    assert "not a valid PNG image" in html


def test_admin_staff_cannot_preview_or_remove(admin_staff, design) -> None:
    _, vp, _ = design
    client = client_for(admin_staff)
    assert client.post(reverse("certificates:signature_preview"), {"file": upload("s.jpg", scanned_signature())}).status_code == 403
    assert client.post(reverse("certificates:signatory_remove_background", args=[vp.pk])).status_code == 403
    with pytest.raises(PermissionDenied):
        services.remove_signature_background(admin_staff, vp)


def test_remove_white_background_creates_a_new_version(generated, director) -> None:
    activity, _ = generated
    person = services.add_signatory(director, "Dr. Scan", "Dean", upload("s.jpg", scanned_signature()), keep_background=True)
    services.set_activity_signatory(director, activity, "right", person)
    assert services.white_background_removable(person)

    response = client_for(director).post(reverse("certificates:signatory_remove_background", args=[person.pk]))

    assert response.status_code == 302
    person.refresh_from_db()
    new = CertificateSignatory.objects.get(previous_version=person)
    assert not person.is_active and new.is_active and new.signature_image_path.name.endswith(".png")
    assert stored(new).getpixel((150, 5))[3] == 0
    assert services.activity_signatories(activity)["right"] == new  # the activity moved to the new version
    assert not services.white_background_removable(new)
    with pytest.raises(services.CertificateError, match="already has a transparent background"):
        services.remove_signature_background(director, new)


def test_the_signatories_page_offers_remove_white_background_only_when_useful(director, design) -> None:
    _, vp, _ = design  # the fixture's images are opaque PNGs
    clear = services.add_signatory(director, "Dr. Clear", "Dean", upload("clear.png", transparent_png()))
    html = client_for(director).get(reverse("certificates:signatories")).content.decode()
    assert f'id="person-background-{vp.pk}"' in html
    assert f'id="person-background-{clear.pk}"' not in html


def test_imaging_keeps_an_existing_alpha_channel() -> None:
    image = Image.new("RGBA", (4, 4), (0, 0, 0, 128))
    assert imaging.has_transparency(image)
    assert imaging.remove_white_background(image).getpixel((0, 0))[3] == 128
