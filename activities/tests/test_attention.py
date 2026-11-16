"""Needs attention (ASSUMPTIONS 347): the items, the parity between the activity page and the list counts, and the
list's fixed number of queries."""

from datetime import datetime, time, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from activities import attention, services
from activities.models import Activity
from registrations import services as registration_services
from registrations.models import EvaluationSubmission, FormType, Registration

pytestmark = pytest.mark.django_db


def manila(day, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def texts(activity: Activity, now: datetime) -> list[str]:
    return [item.text for item in attention.activity_attention(activity, now)]


def test_a_new_draft_needs_its_forms(new_activity) -> None:
    now = manila(new_activity.start_date - timedelta(days=29), 9)
    items = attention.activity_attention(new_activity, now)
    assert [(i.text, i.tab) for i in items] == [
        ("Registration form not built yet.", "forms"),
        ("Evaluation form not built yet.", "forms"),
        # No default signatories in these tests: the slots stay empty (ASSUMPTIONS 366).
        ("Choose who signs the certificates (left and right).", "certificates"),
    ]


def test_missing_times_link_to_the_edit_page(new_activity) -> None:
    Activity.objects.filter(pk=new_activity.pk).update(start_time=None, end_time=None)
    new_activity.refresh_from_db()
    items = attention.activity_attention(new_activity, manila(new_activity.start_date - timedelta(days=29), 9))
    assert items[0] == attention.AttentionItem("Set the start and end time.", "edit")


def test_a_draft_close_to_its_start(new_activity) -> None:
    near = texts(new_activity, manila(new_activity.start_date - timedelta(days=3), 9))
    assert any(text.startswith("Still a Draft but it starts on") for text in near)
    past = texts(new_activity, manila(new_activity.start_date + timedelta(days=1), 9))
    assert any(text.startswith("Still a Draft but it started on") for text in past)


def test_documents_overdue_and_due_soon_use_the_reminder_rule(new_activity) -> None:
    # Placeholders (ASSUMPTIONS 9): Activity Design 10 days before, Request Letter 5 (the Invitation Letter is optional,
    # ASSUMPTIONS 386: never overdue or due soon).
    items = texts(new_activity, manila(new_activity.start_date - timedelta(days=5), 9))
    assert "Overdue required documents: Activity Design." in items
    assert "Required documents due soon and not uploaded: Request Letter." in items
    assert not any("Invitation Letter" in text for text in items)


def test_ongoing_without_attendance_then_ended(new_activity, move_to, register) -> None:
    move_to(new_activity, "Ongoing")
    register(new_activity)
    assert attention.ONGOING_NO_ATTENDANCE in texts(new_activity, manila(new_activity.start_date, 10))
    after = texts(new_activity, manila(new_activity.end_date, 18))
    assert attention.ENDED_NO_ATTENDANCE in after and attention.ONGOING_NO_ATTENDANCE not in after


def test_ended_with_nobody_registered_says_so(new_activity, move_to) -> None:
    """ASSUMPTIONS 353: there is no attendance to mark, so staff conduct it (or change the status) by hand."""
    move_to(new_activity, "Ongoing")
    during = texts(new_activity, manila(new_activity.start_date, 10))
    assert attention.ONGOING_NO_ATTENDANCE in during and attention.NO_REGISTRATIONS not in during
    items = attention.activity_attention(new_activity, manila(new_activity.end_date, 18))
    assert attention.AttentionItem(attention.NO_REGISTRATIONS, "overview", "attendance") in items
    assert attention.ENDED_NO_ATTENDANCE not in [item.text for item in items]
    assert attention.NO_REGISTRATIONS == "No one registered. Mark as Conducted or change the status."


def test_attendance_marked_after_the_evaluation_closed(new_activity, move_to, register, director) -> None:
    """The Ideation Bootcamp case: attendance marked after evaluation_deadline, so these attendees' links never work."""
    form = registration_services.create_form(director, new_activity, FormType.EVALUATION)
    move_to(new_activity, "Ongoing")
    late, on_time, evaluated = register(new_activity, 3)
    registration_services.set_attendance(director, new_activity, [late.pk, on_time.pk, evaluated.pk], True, "email")
    after_deadline = new_activity.evaluation_deadline + timedelta(hours=5)
    Registration.objects.filter(pk__in=[late.pk, evaluated.pk]).update(attendance_marked_at=after_deadline)
    Registration.objects.filter(pk=on_time.pk).update(attendance_marked_at=new_activity.evaluation_deadline - timedelta(days=1))
    services.change_status(director, new_activity, "Conducted")
    now = after_deadline + timedelta(hours=1)
    assert attention.ATTENDANCE_AFTER_EVALUATION in texts(new_activity, now)
    assert attention.ATTENDANCE_AFTER_EVALUATION == "Attendance was marked after the evaluation closed. These attendees can't evaluate."
    # Only attendees who still have to evaluate count: when the late one is the only one and has evaluated, no item
    Registration.objects.filter(pk=late.pk).update(attendance_marked_at=on_time.attendance_marked_at)
    EvaluationSubmission.objects.create(template=form, registration=evaluated, certificate_name="X", certificate_email="x@example.com")
    assert attention.ATTENDANCE_AFTER_EVALUATION not in texts(new_activity, now)


def test_conducted_with_attendees_but_no_certificates(new_activity, move_to, register, director) -> None:
    move_to(new_activity, "Ongoing")
    person = register(new_activity)[0]
    registration_services.set_attendance(director, new_activity, [person.pk], True, "email")
    services.change_status(director, new_activity, "Conducted")
    items = attention.activity_attention(new_activity, manila(new_activity.end_date, 18))
    assert attention.AttentionItem(
        "Conducted with 1 attendee but no certificates yet: they are created automatically once the certificate design "
        "and signatories are set up.", "certificates") in items


def test_certificates_waiting_for_approval(activity, registration, make_certificate, signatories) -> None:
    """One line, the same for everyone (ASSUMPTIONS 361, 369): the missing signature first, otherwise "Certificates need
    approval." while the activity is not approved (evaluated or not); nothing once it is approved."""
    from activities.models import Activity
    from certificates.models import CertificateSignatory
    from django.utils import timezone

    make_certificate(registration)
    now = manila(activity.start_date - timedelta(days=20), 9)
    assert "Certificates need approval." in texts(activity, now)
    CertificateSignatory.objects.filter(pk=signatories[1].pk).update(signature_image_path=None)
    items = texts(activity, now)
    assert "Add the signature of Director (Director) first." in items  # ASSUMPTIONS 363: the person by name
    assert "Certificates need approval." not in items
    Activity.objects.filter(pk=activity.pk).update(certificates_approved_at=timezone.now())
    activity.refresh_from_db()
    assert not [text for text in texts(activity, now) if "approval" in text]


def test_failed_certificate_emails(activity, registration, make_certificate) -> None:
    from django.utils import timezone
    from certificates.models import Certificate

    certificate = make_certificate(registration)
    Certificate.objects.filter(pk=certificate.pk).update(status=Certificate.Status.ISSUED, send_failed_at=timezone.now())
    items = texts(activity, manila(activity.start_date - timedelta(days=20), 9))
    assert "1 certificate could not be emailed. Resend on the Certificates tab." in items
    assert not any("email not sent yet" in item for item in items)


def test_failed_and_unsent_evaluation_links(new_activity, move_to, register, director) -> None:
    registration_services.create_form(director, new_activity, FormType.EVALUATION)
    move_to(new_activity, "Ongoing")
    people = register(new_activity, 3)
    registration_services.set_attendance(director, new_activity, [p.pk for p in people], True, "email")
    services.change_status(director, new_activity, "Conducted")
    Registration.objects.filter(pk=people[0].pk).update(evaluation_link_failed_at=timezone.now())
    Registration.objects.filter(pk=people[1].pk).update(evaluation_link_sent_at=timezone.now())
    items = attention.activity_attention(new_activity, manila(new_activity.end_date, 18))
    assert attention.AttentionItem("1 evaluation link email failed. Resend on the Evaluation tab.", "evaluation") in items
    assert attention.AttentionItem("1 attendee has not been sent an evaluation link yet.", "evaluation") in items


def _activities_in_many_states(director, valid_data, move_to, register) -> list[Activity]:
    draft = services.create_activity(director, valid_data(title="Draft"))
    scheduled = move_to(services.create_activity(director, valid_data(title="Scheduled")), "Scheduled")
    registration_services.create_form(director, scheduled, FormType.REGISTRATION)
    ongoing = move_to(services.create_activity(director, valid_data(title="Ongoing")), "Ongoing")
    conducted = move_to(services.create_activity(director, valid_data(title="Conducted")), "Ongoing")
    person = register(conducted)[0]
    registration_services.set_attendance(director, conducted, [person.pk], True, "email")
    services.change_status(director, conducted, "Conducted")
    no_times = services.create_activity(director, valid_data(title="No times"))
    Activity.objects.filter(pk=no_times.pk).update(start_time=None, end_time=None)
    return list(Activity.objects.select_related("status").order_by("pk"))


def test_page_items_and_list_counts_always_agree(director, valid_data, move_to, register) -> None:
    activities = _activities_in_many_states(director, valid_data, move_to, register)
    start = activities[0].start_date
    for now in (manila(start - timedelta(days=29), 9), manila(start - timedelta(days=5), 9), manila(start, 10),
                manila(start + timedelta(days=1), 18), manila(start + timedelta(days=40), 9)):
        counts = attention.attention_counts(activities, now)
        assert counts == {a.pk: len(attention.activity_attention(a, now)) for a in activities}, now


def test_list_counts_use_a_fixed_number_of_queries(director, valid_data, move_to, register) -> None:
    activities = _activities_in_many_states(director, valid_data, move_to, register)
    now = timezone.now()
    with CaptureQueriesContext(connection) as one:
        attention.attention_counts(activities[:1], now)
    with CaptureQueriesContext(connection) as many:
        attention.attention_counts(activities, now)
    assert len(one) == len(many) <= 7  # + the signatory slots (ASSUMPTIONS 365-367)


def test_list_shows_the_warning_count(client, director, new_activity) -> None:
    client.force_login(director)
    html = client.get(reverse("activities:list")).content.decode()
    assert 'aria-label="3 items need attention"' in html  # the forms + who signs (no defaults in this test)


def test_activity_page_shows_the_box_or_all_set(client, director, new_activity, monkeypatch) -> None:
    client.force_login(director)
    url = reverse("activities:detail", args=[new_activity.pk])
    html = client.get(url).content.decode()
    assert "Needs attention (3)" in html and "Registration form not built yet." in html and "?tab=forms" in html
    monkeypatch.setattr(attention, "activity_attention", lambda activity, now=None: [])
    assert "All set" in client.get(url).content.decode()
