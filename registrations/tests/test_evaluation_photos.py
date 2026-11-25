"""Participant photos with the evaluation (ASSUMPTIONS 435): upload checks and limits, EXIF / GPS removed, random
stored names, the evaluation still submits without photos, nothing left behind on a refusal, staff-only views
(can_create_activity), "Use in reports" with who and when, photos_for_reports, files removed when the activity is
deleted, and never a DOCUMENT."""

import logging
import re
from io import BytesIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from accounts.models import Role
from activities import deletion
from documents.models import Document
from registrations import evaluation_photos, services
from registrations.models import EvaluationAttachment, EvaluationSubmission, Registration

from .conftest import CERT, evaluation_answers
from .test_public_pages import eval_url, evaluation_post, open_window

pytestmark = pytest.mark.django_db
PDF = b"%PDF-1.4\n1 0 obj << /Author (Ana Cruz) >> endobj\ntrailer << >>\n%%EOF\n"
GPS_IFD = 0x8825
ORIENTATION = 0x0112


def jpeg(width: int = 40, height: int = 20, *, gps: bool = False, rotate: bool = False) -> bytes:
    """A JPEG; with gps=True it carries a GPS location and the camera maker in EXIF, with rotate=True the EXIF says
    "turn 90°" (orientation 6), like a phone held upright."""
    exif = Image.Exif()
    if gps:
        exif[0x010F] = "PhoneMaker"
        exif.get_ifd(GPS_IFD).update({1: "N", 2: (8.0, 57.0, 0.0), 3: "E", 4: (125.0, 32.0, 0.0)})
    if rotate:
        exif[ORIENTATION] = 6
    buffer = BytesIO()
    Image.new("RGB", (width, height), "red").save(buffer, "JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def png(text: str = "") -> bytes:
    info = PngInfo()
    if text:
        info.add_text("Comment", text)
    buffer = BytesIO()
    Image.new("RGBA", (30, 30), (0, 0, 255, 128)).save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def upload(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


def stored_files() -> list[str]:
    folder = evaluation_photos.UPLOAD_ROOT
    if not default_storage.exists(folder):
        return []
    names = []
    for activity_folder in default_storage.listdir(folder)[0]:
        names += [f"{folder}/{activity_folder}/{name}" for name in default_storage.listdir(f"{folder}/{activity_folder}")[1]]
    return names


def submit(person, activity, files=None, **extra):
    return services.submit_evaluation(services.evaluation_token(person), **CERT, answers=evaluation_answers(activity),
                                      attachments=files, **extra)


@pytest.fixture
def person(attended, scheduled):
    """One attended person; the evaluation is open now."""
    attendee = attended(1)[0]
    open_window(scheduled)
    return attendee


@pytest.fixture
def photos(person, scheduled):
    """The person's evaluation with a photo (with GPS), a PNG and a PDF."""
    submit(person, scheduled, [upload("IMG_0042.jpg", jpeg(gps=True)), upload("poster.png", png()),
                               upload("Business plan.pdf", PDF)])
    return list(EvaluationAttachment.objects.order_by("pk"))


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def url(name: str, attachment: EvaluationAttachment) -> str:
    return reverse(f"registrations:{name}", args=[attachment.submission.registration.activity_id, attachment.pk])


# --- Submitting -------------------------------------------------------------------------------------------------------


def test_the_evaluation_still_submits_without_photos(person, scheduled) -> None:
    submission = submit(person, scheduled)
    assert submission.pk and not submission.attachments.exists() and stored_files() == []


def test_the_page_submits_without_photos_and_shows_the_upload_section(client, person, scheduled) -> None:
    review = client.post(eval_url(person), evaluation_post(scheduled)).content.decode()
    assert 'enctype="multipart/form-data"' in review and "Upload your photos from the event" in review
    assert re.search(r'<input id="id_photos" type="file" name="photos" multiple', review)
    assert "Up to 5 files, JPG or PNG photos or PDF, at most 10 MB each." in review
    response = client.post(eval_url(person), evaluation_post(scheduled, step="confirm"))
    assert response.status_code == 302 and EvaluationSubmission.objects.count() == 1
    assert not EvaluationAttachment.objects.exists()


def test_the_page_saves_the_photos_with_the_same_submission(client, person, scheduled) -> None:
    files = [upload("IMG_0042.jpg", jpeg()), upload("Business plan.pdf", PDF)]
    response = client.post(eval_url(person), evaluation_post(scheduled, step="confirm", photos=files))
    assert response.status_code == 302
    submission = EvaluationSubmission.objects.get()
    first, second = submission.attachments.order_by("pk")
    assert (first.file_name, first.content_type) == ("IMG_0042.jpg", "image/jpeg")
    assert (second.file_name, second.content_type) == ("Business plan.pdf", "application/pdf")
    for attachment in (first, second):
        assert re.fullmatch(rf"evaluation-photos/activity-{scheduled.pk}/[0-9a-f]{{32}}\.(jpg|pdf)", attachment.file_path.name)
        assert "IMG_0042" not in attachment.file_path.name and "plan" not in attachment.file_path.name
        assert attachment.use_in_reports is False and attachment.marked_by is None and attachment.marked_at is None
        assert attachment.file_size_kb >= 1 and attachment.uploaded_at is not None


def test_a_walk_in_can_add_photos(client, person, scheduled, director) -> None:
    Registration.objects.filter(pk=person.pk).update(registration_source=Registration.Source.WALK_IN)
    client.post(eval_url(person), evaluation_post(scheduled, step="confirm", photos=[upload("me.jpg", jpeg())]))
    assert EvaluationAttachment.objects.count() == 1
    tab = client_for(director).get(reverse("activities:detail", args=[scheduled.pk]) + "?tab=evaluation").content.decode()
    assert "Walk-in" in tab and person.name in tab


# --- Checks and limits ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, content, message", [
    ("virus.exe", b"MZ\x90\x00", "only JPG or PNG photos and PDF files can be uploaded"),
    ("animation.gif", b"GIF89a....", "only JPG or PNG photos and PDF files can be uploaded"),
    ("no-extension", jpeg(), "only JPG or PNG photos and PDF files can be uploaded"),
    ("fake.png", jpeg(), "is not a valid PNG file"),
    ("fake.jpg", b"\xff\xd8\xff" + b"not really a jpeg" * 10, "is not a valid JPG file"),
    ("fake.pdf", b"<html>not a pdf</html>", "is not a valid PDF file"),
    ("empty.jpg", b"", "is empty"),
])
def test_wrong_files_are_refused_and_nothing_is_saved(person, scheduled, name, content, message) -> None:
    with pytest.raises(ValidationError) as error:
        submit(person, scheduled, [upload("ok.jpg", jpeg()), upload(name, content)])
    assert message in error.value.message_dict["photos"][0]
    assert not EvaluationSubmission.objects.exists() and stored_files() == []


def test_at_most_five_files(person, scheduled) -> None:
    with pytest.raises(ValidationError) as error:
        submit(person, scheduled, [upload(f"p{n}.jpg", jpeg()) for n in range(6)])
    assert error.value.message_dict["photos"] == ["Choose at most 5 files."]
    assert not EvaluationSubmission.objects.exists()
    submit(person, scheduled, [upload(f"p{n}.jpg", jpeg()) for n in range(5)])
    assert EvaluationAttachment.objects.count() == 5


def test_a_file_over_the_size_limit_is_refused(person, scheduled, settings) -> None:
    settings.EVALUATION_PHOTO_MAX_MB = 1  # the limit is a setting (10 MB); 1 MB keeps the test small
    big = PDF + b"0" * (1024 * 1024)
    with pytest.raises(ValidationError) as error:
        submit(person, scheduled, [upload("big.pdf", big)])
    assert error.value.message_dict["photos"] == ["“big.pdf” is larger than 1 MB. Choose a smaller file."]
    assert not EvaluationSubmission.objects.exists() and stored_files() == []


def test_a_refused_file_on_the_page_shows_the_review_step_again(client, person, scheduled) -> None:
    response = client.post(eval_url(person), evaluation_post(scheduled, step="confirm", photos=[upload("x.gif", b"GIF89a")]))
    text = response.content.decode()
    assert response.status_code == 400 and not EvaluationSubmission.objects.exists()
    assert "Is this correct?" in text and "only JPG or PNG photos and PDF files can be uploaded" in text
    assert "choose your files again, or submit without them" in text
    assert re.search(r'<input id="id_photos"[^>]*aria-invalid="true" autofocus', text)


def test_a_failure_while_saving_removes_the_files_already_written(person, scheduled, monkeypatch) -> None:
    real_create = EvaluationAttachment.objects.create
    calls = []

    def create_then_fail(**kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("database gone")
        return real_create(**kwargs)

    monkeypatch.setattr(EvaluationAttachment.objects, "create", create_then_fail)
    with pytest.raises(RuntimeError):
        submit(person, scheduled, [upload("a.jpg", jpeg()), upload("b.jpg", jpeg())])
    assert not EvaluationSubmission.objects.exists() and not EvaluationAttachment.objects.exists()
    assert stored_files() == []


def test_the_display_name_never_holds_folders_or_control_characters() -> None:
    assert evaluation_photos.display_name("C:\\Users\\ana\\..\\IMG 1.jpg") == "IMG 1.jpg"
    assert evaluation_photos.display_name("../../etc/pass\x00wd.png") == "passwd.png"
    long_name = evaluation_photos.display_name("a" * 400 + ".jpg")
    assert len(long_name) == 255 and long_name.endswith(".jpg")


# --- Re-encoding: EXIF and GPS removed --------------------------------------------------------------------------------


def test_gps_and_camera_details_are_removed_and_the_photo_is_turned_upright(person, scheduled) -> None:
    original = jpeg(40, 20, gps=True, rotate=True)
    assert Image.open(BytesIO(original)).getexif().get_ifd(GPS_IFD)  # the test photo really has a location
    submit(person, scheduled, [upload("IMG_0042.jpg", original)])
    attachment = EvaluationAttachment.objects.get()
    content = evaluation_photos.file_bytes(attachment)
    image = Image.open(BytesIO(content))
    assert image.format == "JPEG" and len(image.getexif()) == 0 and not image.getexif().get_ifd(GPS_IFD)
    assert b"Exif" not in content and b"PhoneMaker" not in content
    assert image.size == (20, 40)  # orientation 6 applied: upright, no longer needs the EXIF tag


def test_png_text_chunks_are_removed(person, scheduled) -> None:
    submit(person, scheduled, [upload("poster.png", png(text="taken at 8.95N 125.53E"))])
    content = evaluation_photos.file_bytes(EvaluationAttachment.objects.get())
    assert content.startswith(b"\x89PNG") and b"125.53E" not in content
    assert Image.open(BytesIO(content)).mode == "RGBA"  # transparency kept


def test_large_photos_are_shrunk_and_pdfs_are_stored_as_they_are(person, scheduled, settings) -> None:
    settings.EVALUATION_PHOTO_MAX_SIDE = 100
    submit(person, scheduled, [upload("big.jpg", jpeg(400, 300)), upload("work.pdf", PDF)])
    photo, pdf = EvaluationAttachment.objects.order_by("pk")
    assert Image.open(BytesIO(evaluation_photos.file_bytes(photo))).size == (100, 75)
    assert evaluation_photos.file_bytes(pdf) == PDF  # owner decision: PDF metadata is not cleaned


# --- Staff views and permissions --------------------------------------------------------------------------------------


def test_director_sees_downloads_and_opens_the_larger_view(photos, director) -> None:
    client = client_for(director)
    photo, _, pdf = photos
    inline = client.get(url("evaluation_photo", photo))
    assert inline.status_code == 200 and inline["Content-Type"] == "image/jpeg"
    assert inline["Cache-Control"] == "private, no-store" and inline["X-Content-Type-Options"] == "nosniff"
    assert inline["Content-Disposition"].startswith("inline")
    download = client.get(url("evaluation_photo_download", pdf))
    assert download.status_code == 200 and download["Content-Type"] == "application/pdf"
    assert download["Content-Disposition"].startswith("attachment") and "Business plan.pdf" in download["Content-Disposition"]
    dialog = client.get(url("evaluation_photo_dialog", photo)).content.decode()
    assert "IMG_0042.jpg" in dialog and "Ana Cruz" in dialog and url("evaluation_photo_download", photo) in dialog


@pytest.mark.parametrize("who", ["view_only", "admin_member"])
def test_staff_without_can_create_activity_get_403(photos, request, who) -> None:
    client = client_for(request.getfixturevalue(who))
    photo = photos[0]
    for name in ("evaluation_photo", "evaluation_photo_download", "evaluation_photo_dialog"):
        assert client.get(url(name, photo)).status_code == 403
    assert client.post(url("evaluation_photo_mark", photo), {"use_in_reports": "true"}).status_code == 403
    photo.refresh_from_db()
    assert photo.use_in_reports is False


def test_anonymous_visitors_are_sent_to_the_login_page_and_media_is_never_served(client, photos) -> None:
    photo = photos[0]
    response = client.get(url("evaluation_photo", photo))
    assert response.status_code == 302 and "/accounts/login/" in response["Location"]
    assert client.get("/media/" + photo.file_path.name).status_code == 404


def test_a_file_of_another_activity_is_404(photos, director, scheduled) -> None:
    photo = photos[0]
    other = reverse("registrations:evaluation_photo", args=[scheduled.pk + 1000, photo.pk])
    assert client_for(director).get(other).status_code == 404


def test_the_evaluation_tab_shows_files_only_to_can_create_activity(photos, director, view_only, scheduled) -> None:
    tab_url = reverse("activities:detail", args=[scheduled.pk]) + "?tab=evaluation"
    director_tab = client_for(director).get(tab_url).content.decode()
    assert "Participant photos" in director_tab and "3 files uploaded · 0 used in reports" in director_tab
    assert url("evaluation_photo", photos[0]) in director_tab and "IMG_0042.jpg" in director_tab
    assert director_tab.count('name="use_in_reports" value="true"') == 3
    other_tab = client_for(view_only).get(tab_url).content.decode()
    assert "3 files uploaded" in other_tab and "Only staff who can create activities see the files" in other_tab
    assert url("evaluation_photo", photos[0]) not in other_tab and "IMG_0042.jpg" not in other_tab


# --- "Use in reports" -------------------------------------------------------------------------------------------------


def test_use_in_reports_keeps_who_and_when(photos, director, make_member, caplog) -> None:
    from datetime import timedelta

    from django.utils import timezone

    photo = photos[0]
    first = timezone.now()
    with caplog.at_level(logging.INFO, logger="registrations.audit"):
        evaluation_photos.set_use_in_reports(director, photo, True, now=first)
    photo.refresh_from_db()
    assert photo.use_in_reports is True and photo.marked_by == director and photo.marked_at == first
    assert f"Evaluation photo {photo.pk} marked for use in reports by member {director.pk}." in caplog.text
    assert "Ana Cruz" not in caplog.text and "IMG_0042" not in caplog.text  # IDs only

    head = make_member("Incubation Project Head")  # another member who can create activities unticks it later
    evaluation_photos.set_use_in_reports(head, photo, False, now=first + timedelta(minutes=5))
    photo.refresh_from_db()
    assert photo.use_in_reports is False and photo.marked_by == head and photo.marked_at == first + timedelta(minutes=5)


def test_setting_the_same_value_changes_nothing(photos, director) -> None:
    photo = evaluation_photos.set_use_in_reports(director, photos[0], False)
    assert photo.marked_by is None and photo.marked_at is None


def test_the_service_refuses_members_without_the_flag(photos, view_only) -> None:
    with pytest.raises(PermissionDenied):
        evaluation_photos.set_use_in_reports(view_only, photos[0], True)


def test_the_tick_box_posts_with_htmx_and_without(photos, director, scheduled) -> None:
    client = client_for(director)
    photo = photos[0]
    # A ticked box sends the hidden "false" first and then "true": the last value counts.
    response = client.post(url("evaluation_photo_mark", photo), {"use_in_reports": ["false", "true"]}, HTTP_HX_REQUEST="true")
    text = response.content.decode()
    assert response.status_code == 200 and "will be used in reports" in response["HX-Trigger"]
    assert f'id="photo-{photo.pk}"' in text and "checked" in text and "Marked by Test Director" in text
    assert re.search(r'id="photos-summary"[^>]*hx-swap-oob="true">3 files uploaded · 1 used in reports<', text)
    response = client.post(url("evaluation_photo_mark", photo), {"use_in_reports": "false"})
    assert response.status_code == 302 and response["Location"].endswith("?tab=evaluation#photos")
    photo.refresh_from_db()
    assert photo.use_in_reports is False
    assert client.post(url("evaluation_photo_mark", photo), {"use_in_reports": "yes"}).status_code == 400
    get = client.get(url("evaluation_photo_mark", photo))  # a GET goes to the tab (config.links.post_only_or_tab)
    assert get.status_code == 302 and "tab=evaluation" in get["Location"]


def test_only_ticked_files_are_for_reports(photos, director, scheduled) -> None:
    assert list(evaluation_photos.photos_for_reports(scheduled)) == []
    evaluation_photos.set_use_in_reports(director, photos[1], True)
    assert list(evaluation_photos.photos_for_reports(scheduled)) == [photos[1]]
    assert list(evaluation_photos.photos_for_reports([scheduled.pk])) == [photos[1]]


# --- Not documents; deleting ------------------------------------------------------------------------------------------


def test_participant_photos_are_never_documents(photos, scheduled) -> None:
    from assistant.models import DocumentEmbedding
    from documents.services import document_checklist

    assert not Document.objects.exists() and not DocumentEmbedding.objects.exists()
    checklist_text = str(document_checklist(scheduled))
    assert "IMG_0042" not in checklist_text and "Business plan" not in checklist_text


def test_deleting_the_activity_removes_the_files_after_the_commit(photos, scheduled, make_member,
                                                                 django_capture_on_commit_callbacks) -> None:
    sysadmin = make_member(Role.SYSTEM_ADMINISTRATOR)
    names = [photo.file_path.name for photo in photos]
    assert all(default_storage.exists(name) for name in names)
    summary = deletion.deletion_summary(scheduled)
    assert summary.participant_photos == 3
    assert "3 participant photos and files added to evaluations" in deletion.what_will_be_deleted(summary)
    with django_capture_on_commit_callbacks(execute=True):
        record = deletion.delete_activity(sysadmin, scheduled, scheduled.title)
    assert record.counts["participant_photos"] == 3
    assert not EvaluationAttachment.objects.exists()
    assert not any(default_storage.exists(name) for name in names)


def test_deleting_a_registration_removes_its_files(photos, person, django_capture_on_commit_callbacks) -> None:
    names = [photo.file_path.name for photo in photos]
    with django_capture_on_commit_callbacks(execute=True):
        Registration.objects.filter(pk=person.pk).delete()
    assert not any(default_storage.exists(name) for name in names)


def test_a_rolled_back_delete_keeps_the_files(photos, person, django_capture_on_commit_callbacks) -> None:
    from django.db import transaction

    names = [photo.file_path.name for photo in photos]
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError), transaction.atomic():
            EvaluationSubmission.objects.filter(registration=person).delete()
            raise RuntimeError("undo")
    assert callbacks == [] and all(default_storage.exists(name) for name in names)
    assert EvaluationAttachment.objects.count() == 3
