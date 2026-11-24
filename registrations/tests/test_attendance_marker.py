"""Who marked attendance and when, undo rules, the "Attendance opens…" text and the Registrations tab buttons
(ASSUMPTIONS 349)."""

import logging
from datetime import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from activities import services as activity_services
from registrations import services
from registrations.models import EvaluationSubmission, Registration

from .conftest import evaluation_answers, opens_at

pytestmark = pytest.mark.django_db


def test_marking_records_who_and_when(scheduled, signup, director) -> None:
    person = signup(scheduled, "ana@example.com")
    activity_services.change_status(director, scheduled, "Ongoing")
    moment = timezone.make_aware(datetime(2026, 11, 6, 10, 52))
    services.set_attendance(director, scheduled, [person.pk], True, "wet_signature", now=moment)
    person.refresh_from_db()
    assert person.attendance_confirmed and person.confirmation_method == "wet_signature"
    assert person.attendance_marked_by == director and person.attendance_marked_at == moment


def test_undo_clears_method_marker_and_time(attended, director, scheduled) -> None:
    person = attended(1)[0]
    services.set_attendance(director, scheduled, [person.pk], False)
    person.refresh_from_db()
    assert not person.attendance_confirmed
    assert person.confirmation_method is None and person.attendance_marked_by is None and person.attendance_marked_at is None


def test_marks_and_undos_are_logged_with_ids_only(attended, director, scheduled, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="registrations.audit"):
        person = attended(1)[0]
        services.set_attendance(director, scheduled, [person.pk], False)
    text = caplog.text
    assert f"registrations [{person.pk}], by member {director.pk}" in text
    assert "marked (email)" in text and "undone" in text
    assert person.name not in text and person.email not in text


def test_undo_is_refused_once_evaluated(attended, director, scheduled) -> None:
    person = attended(1)[0]
    services.submit_evaluation(services.evaluation_token(person), answers=evaluation_answers(scheduled),
                               certificate_name="Ana Cruz", certificate_email="ana@example.com", now=opens_at(scheduled))
    assert services.undo_refused_reason(Registration.objects.get(pk=person.pk)) == services.UNDO_REFUSED_EVALUATED
    with pytest.raises(services.RegistrationError, match="already submitted the evaluation"):
        services.set_attendance(director, scheduled, [person.pk], False)


def test_attendance_opens_text_before_the_start(scheduled) -> None:
    expected = f"Attendance opens when the activity starts on {scheduled.start_date:%b} {scheduled.start_date.day}, {scheduled.start_date.year}, 8:00 AM."
    assert services.attendance_closed_reason(scheduled) == expected


def test_the_tab_shows_the_same_reason_and_no_buttons(client, director, scheduled, signup) -> None:
    signup(scheduled, "ana@example.com")
    client.force_login(director)
    html = client.get(reverse("activities:detail", args=[scheduled.pk]) + "?tab=registrations").content.decode()
    assert services.attendance_closed_reason(scheduled) in html
    assert "Mark selected as attended" not in html and 'name="row_action"' not in html


def test_row_buttons_marker_and_confirmed_by_choice(client, director, attended, scheduled) -> None:
    people = attended(2)
    client.force_login(director)
    services.set_attendance(director, scheduled, [people[1].pk], False)
    html = client.get(reverse("activities:detail", args=[scheduled.pk]) + "?tab=registrations").content.decode()
    assert "Confirmed by:" in html and 'value="wet_signature" class="size-4 accent-primary" checked' in html
    assert f'value="undo:{people[0].pk}"' in html and f'value="mark:{people[1].pk}"' in html
    assert f"Marked by {director.name}" in html
    assert "Mark selected as attended" in html and "Undo selected" in html


def test_row_mark_uses_the_confirmed_by_choice(client, director, scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com")
    other = signup(scheduled, "ben@example.com", name="Ben")
    activity_services.change_status(director, scheduled, "Ongoing")
    client.force_login(director)
    url = reverse("registrations:attendance", args=[scheduled.pk])
    # The row button sends only its own registration, even when other rows are checked.
    client.post(url, {"row_action": f"mark:{person.pk}", "method": "email", "registration_ids": [other.pk]})
    person.refresh_from_db(), other.refresh_from_db()
    assert person.attendance_confirmed and person.confirmation_method == "email" and person.attendance_marked_by == director
    assert not other.attendance_confirmed
    client.post(url, {"action": "undo", "registration_ids": [person.pk]})
    person.refresh_from_db()
    assert not person.attendance_confirmed


def test_row_undo_is_hidden_with_the_reason_once_evaluated(client, director, attended, scheduled) -> None:
    person = attended(1)[0]
    EvaluationSubmission.objects.create(template=services.get_form(scheduled, "evaluation"), registration=person,
                                        certificate_name="Ana", certificate_email="ana@example.com")
    client.force_login(director)
    html = client.get(reverse("activities:detail", args=[scheduled.pk]) + "?tab=registrations").content.decode()
    assert f'value="undo:{person.pk}"' not in html and services.UNDO_REFUSED_EVALUATED in html


def test_django_admin_action_records_the_marker(director, scheduled, signup, monkeypatch) -> None:
    from django.contrib import admin
    from django.test import RequestFactory

    from registrations.admin import RegistrationAdmin

    person = signup(scheduled, "ana@example.com")
    activity_services.change_status(director, scheduled, "Ongoing")
    model_admin = RegistrationAdmin(Registration, admin.site)
    monkeypatch.setattr(model_admin, "message_user", lambda *args, **kwargs: None)
    request = RequestFactory().post("/admin/")
    request.user = director
    model_admin.mark_attended_wet_signature(request, Registration.objects.filter(pk=person.pk))
    person.refresh_from_db()
    assert person.attendance_marked_by == director and person.attendance_marked_at is not None
    model_admin.unmark_attendance(request, Registration.objects.filter(pk=person.pk))
    person.refresh_from_db()
    assert person.attendance_marked_by is None and person.attendance_marked_at is None


def test_upload_box_text(client, director, scheduled) -> None:
    client.force_login(director)
    html = client.get(reverse("activities:detail", args=[scheduled.pk]) + "?tab=documents").content.decode()
    assert "Upload the approved copy: it is saved as Approved and counts at once." in html


def test_time_is_in_the_confirmation_email(scheduled, signup) -> None:
    from django.core import mail

    from registrations import emails

    emails.send_registration_confirmation(signup(scheduled, "ana@example.com"))
    assert "Time: 8:00 AM – 5:00 PM" in mail.outbox[-1].body


def test_attendance_sheet_shows_the_time(client, director, scheduled) -> None:
    client.force_login(director)
    html = client.get(reverse("registrations:attendance_sheet", args=[scheduled.pk])).content.decode()
    assert "8:00 AM – 5:00 PM" in html
