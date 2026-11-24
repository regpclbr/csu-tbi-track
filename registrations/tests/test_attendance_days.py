"""QR attendance per day, walk-ins, cancellation and participant signatures (ASSUMPTIONS 434).

The `scheduled` activity: two days (start in 30 days), 8:00 AM - 5:00 PM, Scheduled, both forms, one category.
"""

import base64
from datetime import datetime, time, timedelta
from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from activities import services as activity_services
from activities.models import Activity
from registrations import attendance, services, signatures
from registrations.models import AttendanceDay, ParticipantSignature, Registration

from .conftest import FIXED

pytestmark = pytest.mark.django_db

TYPED = {"kind": "typed", "typed_name": "Ana Cruz", "confirmed": True}


def png(width=60, height=30, ink=True, kind="PNG") -> bytes:
    image = Image.new("RGBA" if kind == "PNG" else "RGB", (width, height), (255, 255, 255, 0) if kind == "PNG" else "white")
    if ink:
        for x in range(5, 40):
            image.putpixel((x, 15), (0, 0, 0, 255) if kind == "PNG" else (0, 0, 0))
    buffer = BytesIO()
    image.save(buffer, kind)
    return buffer.getvalue()


def drawn(ink=True) -> dict:
    return {"kind": "draw", "drawn": "data:image/png;base64," + base64.b64encode(png(ink=ink)).decode()}


def at(day, hour, minute=0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


# --- The check-in window, per day ---------------------------------------------------------------------------------


def test_window_opens_an_hour_early_on_every_day_and_closes_at_the_end(scheduled, settings) -> None:
    settings.PARTICIPANT_CHECKIN_EARLY_MINUTES = 60
    first, second = scheduled.start_date, scheduled.end_date
    assert attendance.checkin_state(scheduled, at(first, 6, 59)).kind == "not_yet_open"
    assert "Check-in opens on" in attendance.checkin_state(scheduled, at(first, 6, 59)).reason
    state = attendance.checkin_state(scheduled, at(first, 7, 0))  # day 1, still Scheduled
    assert (state.kind, state.day) == ("open", first)
    assert attendance.checkin_state(scheduled, at(first, 16, 59)).kind == "open"
    between = attendance.checkin_state(scheduled, at(first, 17, 0))  # after day 1, before day 2
    assert between.kind == "not_yet_open" and between.day == second
    assert attendance.checkin_state(scheduled, at(second, 7, 30)).day == second
    assert attendance.checkin_state(scheduled, at(second, 17, 0)).kind == "closed"


def test_window_without_times_is_the_whole_day(scheduled) -> None:
    Activity.objects.filter(pk=scheduled.pk).update(start_time=None, end_time=None)
    scheduled.refresh_from_db()
    assert attendance.checkin_state(scheduled, at(scheduled.start_date, 0, 1)).kind == "open"
    assert attendance.checkin_state(scheduled, at(scheduled.end_date, 23, 59)).kind == "open"


def test_draft_is_not_available_and_conducted_is_closed(scheduled, director, valid_data) -> None:
    draft = activity_services.create_activity(director, valid_data(title="Still a draft"))
    assert attendance.checkin_state(draft).kind == "not_available"
    activity_services.change_status(director, scheduled, "Ongoing")
    activity_services.change_status(director, scheduled, "Conducted")
    assert attendance.checkin_state(scheduled, at(scheduled.start_date, 9)).kind == "closed"


def test_check_in_outside_the_window_is_refused(scheduled, signup) -> None:
    signup(scheduled, "ana@example.com")
    with pytest.raises(attendance.AttendanceError, match="Check-in opens on"):
        attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.start_date, 6))


# --- QR check-in ----------------------------------------------------------------------------------------------------


def test_check_in_records_today_once_per_day(scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com", signature=TYPED)
    first = attendance.check_in(scheduled, " ANA@example.com ", now=at(scheduled.start_date, 8, 42))
    assert first.kind == "checked_in" and first.row.method == "qr" and first.row.day == scheduled.start_date
    again = attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.start_date, 9, 15))
    assert again.kind == "already" and again.message == "Already checked in today at 8:42 AM."
    second_day = attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.end_date, 8))
    assert second_day.kind == "checked_in"
    person.refresh_from_db()
    assert person.attendance_confirmed and person.attendance_days.count() == 2


def test_check_in_sends_the_notice_email(scheduled, signup, mailoutbox, monkeypatch, django_capture_on_commit_callbacks) -> None:
    from registrations import tasks

    monkeypatch.setattr(attendance, "_queue_checkin_email", tasks.run_send_checkin_email)
    signup(scheduled, "ana@example.com", signature=TYPED)
    mailoutbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.start_date, 8, 42))
    body = mailoutbox[-1].body
    assert f"You were checked in to {scheduled.title} on" in body and "8:42 AM. If this wasn't you, contact the TBI." in body


def test_check_in_failure_to_email_never_blocks(scheduled, signup, monkeypatch, django_capture_on_commit_callbacks) -> None:
    def broken(row_id):
        raise RuntimeError("queue down")

    monkeypatch.setattr(attendance, "_queue_checkin_email", broken)
    person = signup(scheduled, "ana@example.com", signature=TYPED)
    with django_capture_on_commit_callbacks(execute=True):
        assert attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.start_date, 8)).kind == "checked_in"
    assert person.attendance_days.exists()


def test_signature_is_required_before_the_check_in_finishes(scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com")  # an older registration: no signature
    result = attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.start_date, 8))
    assert result.kind == "needs_signature" and not person.attendance_days.exists()
    with pytest.raises(ValidationError):
        attendance.check_in(scheduled, "ana@example.com", signature={"kind": "typed", "typed_name": "Ana"},
                            now=at(scheduled.start_date, 8))  # not confirmed
    assert attendance.check_in(scheduled, "ana@example.com", signature=drawn(), now=at(scheduled.start_date, 8)).kind == "checked_in"
    assert ParticipantSignature.objects.get(registration=person).kind == "draw"


def test_unknown_email_is_offered_the_walk_in_form(scheduled) -> None:
    assert attendance.check_in(scheduled, "nobody@example.com", now=at(scheduled.start_date, 8)).kind == "not_registered"
    with pytest.raises(ValidationError):
        attendance.check_in(scheduled, "not-an-email", now=at(scheduled.start_date, 8))


def test_a_cancelled_registration_can_still_check_in(scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com", signature=TYPED)
    services.cancel_registration(person, now=at(scheduled.start_date - timedelta(days=3), 9))
    assert attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.start_date, 8)).kind == "checked_in"
    person.refresh_from_db()
    assert person.cancelled_at is not None and person.attendance_confirmed


# --- Walk-ins ---------------------------------------------------------------------------------------------------


def walk_in(activity, email="walk@example.com", now=None, **extra):
    values = {"name": "Walter In", "email": email, "contact_number": "0917", **FIXED, "signature": TYPED, **extra}
    return attendance.register_walk_in(activity, now=now or at(activity.start_date, 9), **values)


def test_walk_in_registers_and_checks_in_even_when_registration_is_closed(scheduled, director) -> None:
    activity_services.set_registration_closed_by_hand(director, scheduled, True)
    Activity.objects.filter(pk=scheduled.pk).update(max_slots=1)
    result = walk_in(scheduled)
    registration = result.registration
    assert registration.registration_source == Registration.Source.WALK_IN
    assert registration.attendance_confirmed and result.row.method == "qr"
    assert registration.signature.typed_name == "Ana Cruz"
    assert walk_in(scheduled, email="second@example.com").kind == "checked_in"  # max_slots does not apply


def test_walk_in_same_email_twice_is_refused(scheduled, signup) -> None:
    signup(scheduled, "ana@example.com")
    with pytest.raises(services.RegistrationError, match="already registered for this activity"):
        walk_in(scheduled, email="ANA@example.com")
    walk_in(scheduled)
    with pytest.raises(services.RegistrationError, match="already registered for this activity"):
        walk_in(scheduled)


def test_walk_in_needs_every_field_and_a_signature(scheduled) -> None:
    with pytest.raises(ValidationError) as error:
        walk_in(scheduled, name="", designation="", signature={})
    assert {"name", "designation", "signature"} <= set(error.value.message_dict)


def test_walk_in_only_inside_the_window(scheduled) -> None:
    with pytest.raises(attendance.AttendanceError):
        walk_in(scheduled, now=at(scheduled.start_date - timedelta(days=1), 9))


# --- Staff marking per day -------------------------------------------------------------------------------------


def test_staff_mark_one_day_and_undo_keeps_the_other(scheduled, signup, director, clock) -> None:
    person = signup(scheduled, "ana@example.com")
    clock(datetime.combine(scheduled.end_date, time(10)))
    activity_services.change_status(director, scheduled, "Ongoing")
    for day in (scheduled.start_date, scheduled.end_date):
        services.set_attendance(director, scheduled, [person.pk], True, day=day)
    services.set_attendance(director, scheduled, [person.pk], False, day=scheduled.start_date)
    person.refresh_from_db()
    assert person.attendance_confirmed and list(person.attendance_days.values_list("day", flat=True)) == [scheduled.end_date]


def test_staff_cannot_mark_a_day_outside_the_activity(scheduled, signup, director) -> None:
    person = signup(scheduled, "ana@example.com")
    activity_services.change_status(director, scheduled, "Ongoing")
    with pytest.raises(services.RegistrationError, match="Choose a day of this activity"):
        services.set_attendance(director, scheduled, [person.pk], True, day=scheduled.end_date + timedelta(days=1))


def test_staff_collects_a_signature_on_their_device(scheduled, signup, director) -> None:
    person = signup(scheduled, "ana@example.com")
    activity_services.change_status(director, scheduled, "Ongoing")
    with pytest.raises(ValidationError):  # no upload on the staff device (draw or typed only)
        services.set_attendance(director, scheduled, [person.pk], True,
                                signature={"kind": "upload", "file": SimpleUploadedFile("s.png", png())})
    services.set_attendance(director, scheduled, [person.pk], True, signature=drawn())
    assert ParticipantSignature.objects.get(registration=person).kind == "draw"


def test_the_flag_always_matches_the_day_rows(attended, director, scheduled) -> None:
    """Parity: REGISTRATION.attendance_confirmed = has an ATTENDANCE_DAY row, after every service change."""
    people = attended(3)
    services.set_attendance(director, scheduled, [people[0].pk], False)
    for registration in Registration.objects.filter(activity=scheduled):
        assert registration.attendance_confirmed == registration.attendance_days.exists()


def test_dates_cannot_move_away_from_recorded_attendance(attended, director, scheduled) -> None:
    attended(1)  # on the first day
    with pytest.raises(ValidationError, match="Attendance is already recorded on"):
        activity_services.update_activity(director, scheduled, {"start_date": scheduled.end_date})


# --- Cancelling ("I can no longer attend") ---------------------------------------------------------------------------


def test_cancel_frees_the_slot_and_keeps_the_row(scheduled, signup) -> None:
    Activity.objects.filter(pk=scheduled.pk).update(max_slots=1)
    scheduled.refresh_from_db()
    person = signup(scheduled, "ana@example.com")
    assert services.registration_closed_reason(scheduled) == "Registration is closed: all slots are taken."
    services.cancel_registration(person)
    assert Registration.objects.filter(pk=person.pk, cancelled_at__isnull=False).exists()
    assert services.registration_closed_reason(scheduled) is None
    assert scheduled in services.open_activities()  # the SQL twin agrees (registered_count)
    signup(scheduled, "ben@example.com", name="Ben")  # the freed slot
    with pytest.raises(services.RegistrationError, match="all slots are taken"):
        services.restore_registration(person)


def test_cancel_refused_after_the_start_or_a_check_in(scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com", signature=TYPED)
    started = at(scheduled.start_date, 8, 1)
    assert services.cancellation_state(person, started).reason == services.CANCEL_STARTED
    attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.start_date, 7, 30))
    person.refresh_from_db()
    assert services.cancellation_state(person, at(scheduled.start_date, 7, 45)).reason == services.CANCEL_ATTENDED


def test_cancel_page_get_never_cancels_and_post_does(client, scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com")
    url = reverse("cancel_registration", args=[services.cancel_token(person)])
    html = client.get(url).content.decode()
    assert "I can no longer attend" in html and person.email not in html and person.name not in html
    person.refresh_from_db()
    assert person.cancelled_at is None
    client.post(url, {"action": "cancel"})
    person.refresh_from_db()
    assert person.cancelled_at is not None
    assert "I can attend after all" in client.get(url).content.decode()
    client.post(url, {"action": "restore"})
    person.refresh_from_db()
    assert person.cancelled_at is None
    assert client.get(reverse("cancel_registration", args=["forged"])).status_code == 404


def test_confirmation_email_has_the_cancel_link(scheduled, signup, mailoutbox, django_capture_on_commit_callbacks) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        person = signup(scheduled, "ana@example.com")
    assert services.cancel_link(person) in mailoutbox[-1].body


def test_pre_registration_counts_for_the_meal_estimate(scheduled, signup) -> None:
    signup(scheduled, "a@example.com")
    services.cancel_registration(signup(scheduled, "b@example.com", name="B"))
    walk_in(scheduled)
    assert attendance.pre_registration_counts(scheduled) == {"will_attend": 1, "will_not_attend": 1, "walk_ins": 1}


# --- Signatures: the three kinds and the upload checks ----------------------------------------------------------------


def test_three_kinds_are_saved(scheduled, signup) -> None:
    upload = {"kind": "upload", "file": SimpleUploadedFile("sig.jpg", png(kind="JPEG"), content_type="image/jpeg")}
    for email, signature in (("d@example.com", drawn()), ("u@example.com", upload), ("t@example.com", TYPED)):
        signup(scheduled, email, signature=signature)
    kinds = {s.registration.email: s for s in ParticipantSignature.objects.select_related("registration")}
    assert kinds["t@example.com"].typed_name == "Ana Cruz" and not kinds["t@example.com"].file_path
    for email in ("d@example.com", "u@example.com"):
        content = signatures.image_bytes(kinds[email])
        assert content.startswith(signatures.PNG_SIGNATURE)  # always re-encoded to PNG
        assert kinds[email].file_path.name.startswith(f"signatures/activity-{scheduled.pk}/")


@pytest.mark.parametrize(("raw", "message"), [
    ({}, signatures.REQUIRED),
    ({"kind": "selfie"}, signatures.CHOOSE_KIND),
    ({"kind": "draw", "drawn": ""}, signatures.DRAW_FIRST),
    ({"kind": "typed", "typed_name": "   ", "confirmed": True}, signatures.TYPED_REQUIRED),
    ({"kind": "typed", "typed_name": "12345", "confirmed": True}, signatures.TYPED_LETTERS),
    ({"kind": "typed", "typed_name": "Ana", "confirmed": False}, signatures.TYPED_CONFIRM),
    ({"kind": "upload"}, signatures.UPLOAD_CHOOSE),
])
def test_signature_refusals(raw, message) -> None:
    with pytest.raises(ValidationError) as error:
        signatures.clean_signature(raw)
    assert error.value.message_dict == {"signature": [message]}


def test_blank_drawing_is_refused() -> None:
    with pytest.raises(ValidationError, match="Draw your signature"):
        signatures.clean_signature(drawn(ink=False))


def test_upload_checks_the_name_the_content_and_the_size(settings) -> None:
    def upload(name, content):
        return {"kind": "upload", "file": SimpleUploadedFile(name, content)}

    with pytest.raises(ValidationError, match="Only JPG and PNG"):
        signatures.clean_signature(upload("sig.gif", b"GIF89a..."))
    with pytest.raises(ValidationError, match="not a valid JPG or PNG"):
        signatures.clean_signature(upload("sig.png", b"%PDF-1.7 not an image"))
    with pytest.raises(ValidationError, match="not a valid JPG or PNG"):
        signatures.clean_signature(upload("sig.png", signatures.PNG_SIGNATURE + b"broken"))
    settings.PARTICIPANT_SIGNATURE_MAX_KB = 1
    with pytest.raises(ValidationError, match="larger than 1 KB"):
        signatures.clean_signature(upload("sig.png", png(400, 400) + b"0" * 2048))


def test_large_images_are_scaled_down(settings) -> None:
    clean = signatures.clean_signature({"kind": "upload", "file": SimpleUploadedFile("s.png", png(1800, 600))})
    with Image.open(BytesIO(clean.png)) as image:
        assert image.size[0] <= settings.PARTICIPANT_SIGNATURE_MAX_PIXELS[0]


def test_public_registration_requires_a_signature(client, scheduled) -> None:
    from registrations.tests.conftest import registration_answers

    data = {"name": "Ana", "email": "ana@example.com", **FIXED, "privacy_consent": "yes",
            **{f"field_{k}": v for k, v in registration_answers(scheduled).items()}}
    response = client.post(reverse("register", args=[scheduled.pk]), data)
    assert response.status_code == 400 and signatures.REQUIRED in response.content.decode()
    data.update({"signature_kind": "typed", "signature_typed_name": "Ana", "signature_confirm": "yes"})
    assert client.post(reverse("register", args=[scheduled.pk]), data).status_code == 302
    assert Registration.objects.get(email="ana@example.com").signature.kind == "typed"


def test_json_registration_requires_a_signature(client, scheduled) -> None:
    import json

    from registrations.tests.conftest import registration_answers

    data = {"name": "Ana", "email": "ana@example.com", **FIXED, "privacy_consent": True,
            "answers": registration_answers(scheduled)}
    url = f"/api/register/{scheduled.pk}/"
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 400 and "signature" in response.json()["errors"]
    data["signature"] = {"kind": "draw", "image": drawn()["drawn"]}
    assert client.post(url, json.dumps(data), content_type="application/json").status_code == 201


# --- Retention ------------------------------------------------------------------------------------------------------


def _documents(activity, member, names, now):
    from documents.models import DocumentType
    from documents.services import upload_document

    for name in names:
        document_type, _ = DocumentType.objects.get_or_create(name=name, defaults={"is_required": True})
        upload_document(member, activity, document_type, SimpleUploadedFile("x.pdf", b"%PDF-1.7 test"), title=name, now=now)


def test_signatures_are_deleted_one_year_after_the_end(scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com", signature=drawn())
    stored = person.signature.file_path.name
    from django.core.files.storage import default_storage

    assert default_storage.exists(stored)
    signatures.purge_signatures(now=signatures.retention_ends(scheduled) - timedelta(minutes=1))
    assert not ParticipantSignature.objects.get(registration=person).is_deleted
    counts = signatures.purge_signatures(now=signatures.retention_ends(scheduled))
    signature = ParticipantSignature.objects.get(registration=person)
    assert counts == {"activities": 1, "signatures": 1}
    assert signature.is_deleted and not signature.file_path and signature.typed_name is None


def test_signatures_are_deleted_once_the_forms_are_kept_and_newer_than_the_last_change(
        scheduled, signup, director, django_capture_on_commit_callbacks) -> None:
    person = signup(scheduled, "ana@example.com", signature=TYPED)
    activity_services.change_status(director, scheduled, "Ongoing")
    services.set_attendance(director, scheduled, [person.pk], True)
    activity_services.change_status(director, scheduled, "Conducted")
    scheduled.refresh_from_db()
    now = timezone.now()
    _documents(scheduled, director, ["Attendance Sheet", "Activity Report"], now)
    assert signatures.signatures_due(scheduled) is None  # the Confirmation of Attendance is missing
    _documents(scheduled, director, ["Confirmation of Attendance"], now)
    assert signatures.signatures_due(scheduled) == "forms kept"
    # A later change (someone marked, a signature given) means the kept forms no longer show everyone: wait.
    Registration.objects.filter(pk=person.pk).update(updated_at=timezone.now() + timedelta(minutes=5))
    assert signatures.signatures_due(scheduled) is None


def test_generating_after_deletion_is_refused(attended, director, scheduled) -> None:
    from registrations import attendance_forms

    person = attended(1)[0]
    signatures.save_signature(person, signatures.clean_signature(TYPED))
    signatures.delete_signatures(scheduled)
    assert attendance_forms.refused_reason(scheduled, attendance_forms.ATTENDANCE) == attendance_forms.SIGNATURES_DELETED


# --- Public check-in page ---------------------------------------------------------------------------------------------


def test_check_in_page_steps(client, scheduled, signup, clock) -> None:
    signup(scheduled, "ana@example.com")  # no signature yet
    clock(datetime.combine(scheduled.start_date, time(7, 30)))
    url = reverse("checkin", args=[scheduled.pk])
    assert "Attendance check-in" in client.get(url).content.decode()
    html = client.post(url, {"step": "email", "email": "ana@example.com"}).content.decode()
    assert 'data-checkin-step="signature"' in html
    response = client.post(url, {"step": "signature", "email": "ana@example.com", "signature_kind": "typed",
                                 "signature_typed_name": "Ana Cruz", "signature_confirm": "yes", "privacy_consent": "yes"})
    assert response.status_code == 302
    assert "You&#x27;re checked in for" in client.get(response["Location"]).content.decode()
    again = client.post(url, {"step": "email", "email": "ana@example.com"})
    assert "Already checked in today at" in client.get(again["Location"]).content.decode()
    walk = client.post(url, {"step": "email", "email": "new@example.com"}).content.decode()
    assert 'data-checkin-step="walkin"' in walk


def test_check_in_page_before_the_window_shows_when_it_opens(client, scheduled) -> None:
    assert "Check-in opens on" in client.get(reverse("checkin", args=[scheduled.pk])).content.decode()


def test_check_in_page_is_rate_limited_per_email_and_per_ip(client, scheduled, settings, clock) -> None:
    clock(datetime.combine(scheduled.start_date, time(8)))
    settings.CHECKIN_MAX_ATTEMPTS_PER_EMAIL = 2
    url = reverse("checkin", args=[scheduled.pk])
    for _ in range(2):
        assert client.post(url, {"step": "email", "email": "x@example.com"}).status_code == 200
    assert client.post(url, {"step": "email", "email": "x@example.com"}).status_code == 429
    settings.CHECKIN_MAX_ATTEMPTS_PER_IP = 3
    assert client.post(url, {"step": "email", "email": "y@example.com"}).status_code == 429  # 4th from this IP
    honeypot = client.post(url, {"step": "email", "email": "z@example.com", "website": "spam"})
    assert honeypot.status_code in (400, 429)


def test_check_in_page_draft_is_404(client, director, valid_data) -> None:
    draft = activity_services.create_activity(director, valid_data(title="Still a draft"))
    assert client.get(reverse("checkin", args=[draft.pk])).status_code == 404


def test_share_card_shows_the_check_in_link_and_qr(client, director, scheduled) -> None:
    client.force_login(director)
    html = client.get(reverse("activities:detail", args=[scheduled.pk])).content.decode()
    assert "Attendance check-in" in html and attendance.checkin_url(scheduled) in html
    assert client.get(reverse("activities:qr_code", args=[scheduled.pk, "checkin"])).status_code == 200
    assert client.get(reverse("registrations:checkin_poster", args=[scheduled.pk])).status_code == 200
