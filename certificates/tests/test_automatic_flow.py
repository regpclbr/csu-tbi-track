"""The automatic certificate flow (ASSUMPTIONS 362): the System creates the certificates when the activity becomes
Conducted (by hand, automatically, the 5-minute job, attendance marked later), undoing attendance deletes a waiting
certificate, "ready for approval" (computed), approving on the preview and "Approve all ready certificates", and the
counts (S5, Needs attention) that include only ready certificates."""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from activities import attention
from activities import services as activity_services
from activities.models import ActivityAuditLog
from activities.services import activity_start_end
from certificates import services
from certificates.models import Certificate, CertificateSignatory
from registrations import services as registration_services
from registrations.models import Registration
from reports import metrics

from .conftest import S, appreciation_of, certificate_of

pytestmark = pytest.mark.django_db

NOW_FILTERS = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))  # "now" cards ignore the range


def history(activity) -> list[tuple]:
    return list(ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificates")
                .order_by("pk").values_list("changed_by", "old_value", "new_value"))


def without_image(signatory: CertificateSignatory) -> None:
    CertificateSignatory.objects.filter(pk=signatory.pk).update(signature_image_path=None)


@pytest.fixture
def ongoing(attended, director):
    """An Ongoing activity: 2 attendees, 1 registrant who did not attend, 1 resource person (no certificates yet)."""
    people = attended(2)
    activity = people[0].activity
    Registration.objects.create(
        activity=activity, template=people[0].template, participant_category=people[0].participant_category,
        name="No Show", email="noshow@example.com",
    )
    speaker = activity_services.create_resource_person(director, "Dr. Maria Santos", "maria@example.com")
    activity_services.add_resource_person(director, activity, speaker, topic="Pitch Decks")
    activity.refresh_from_db()
    return activity, people


# --- B1: created by the System -----------------------------------------------------------------------------------


def test_conducted_by_hand_creates_the_certificates(ongoing, design, director) -> None:
    activity, people = ongoing
    activity_services.change_status(director, activity, "Conducted")
    assert Certificate.objects.filter(activity=activity, recipient_type="registration").count() == 2
    assert appreciation_of(activity).delivery_method == Certificate.DeliveryMethod.HARD_COPY
    assert not Certificate.objects.filter(registration__name="No Show").exists()
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}
    # One History row by System ("Created automatically")
    assert history(activity) == [(None, "", "3 created automatically (2 Participation, 1 Appreciation)")]


def test_the_automatic_move_creates_them_too(ongoing, design) -> None:
    activity, _ = ongoing
    _, end = activity_start_end(activity)
    assert activity_services.apply_automatic_statuses(now=end + timedelta(hours=1)) == 1
    activity.refresh_from_db()
    assert activity.status.name == "Conducted" and Certificate.objects.filter(activity=activity).count() == 3


def test_running_twice_creates_no_duplicates(ongoing, design, director) -> None:
    activity, _ = ongoing
    activity_services.change_status(director, activity, "Conducted")
    assert services.generate_missing_certificates(activity) == {"participation_created": 0, "appreciation_created": 0, "total": 3}
    assert services.generate_pending_certificates() == 0
    assert Certificate.objects.count() == 3 and len(history(activity)) == 1


def test_an_incomplete_design_never_blocks_the_move_and_the_job_catches_up(ongoing, director) -> None:
    activity, _ = ongoing
    activity_services.change_status(director, activity, "Conducted")  # no template or signatories yet
    activity.refresh_from_db()
    assert activity.status.name == "Conducted" and not Certificate.objects.exists()
    assert "no active certificate design" in services.generation_closed_reason(activity)
    texts = [item.text for item in attention.activity_attention(activity)]
    assert any(text.startswith("Conducted with 2 attendees but no certificates yet") for text in texts)
    assert list(services.activities_missing_certificates()) == [activity]


def test_the_five_minute_job_creates_the_missing_ones(ongoing, director, request) -> None:
    activity, _ = ongoing
    activity_services.change_status(director, activity, "Conducted")
    request.getfixturevalue("design")  # the design is set up afterwards
    from activities import tasks

    result = tasks.run_activity_automation()
    assert result["certificates_created"] == 3 and Certificate.objects.filter(activity=activity).count() == 3
    assert history(activity) == [(None, "", "3 created automatically (2 Participation, 1 Appreciation)")]


def test_attendance_marked_after_conducted_creates_that_certificate(ongoing, design, director) -> None:
    activity, _ = ongoing
    activity_services.change_status(director, activity, "Conducted")
    late = Registration.objects.get(name="No Show")
    registration_services.set_attendance(director, activity, [late.pk], True, "wet_signature")
    assert certificate_of(late).status == S.GENERATED
    assert history(activity)[-1] == (None, "", "1 created automatically (1 Participation)")


def test_the_history_sentence_says_created_automatically(ongoing, design, director) -> None:
    from activities.formatting import audit_sentence

    activity, _ = ongoing
    activity_services.change_status(director, activity, "Conducted")
    entry = ActivityAuditLog.objects.get(activity=activity, field_changed="Certificates")
    assert audit_sentence(entry) == (f"System: certificates of {activity.title}: 3 created automatically "
                                     "(2 Participation, 1 Appreciation)")


# --- Undoing attendance after Conducted --------------------------------------------------------------------------


def test_undo_deletes_a_waiting_certificate_and_logs_it(generated, director) -> None:
    activity, people = generated
    number = certificate_of(people[0]).certificate_number
    assert registration_services.set_attendance(director, activity, [people[0].pk], False) == 1
    assert not Certificate.objects.filter(registration=people[0]).exists()
    assert history(activity)[-1] == (director.pk, f"{number} ({people[0].name})", "Deleted: attendance undone")
    # The number is never reused (a gap)
    registration_services.set_attendance(director, activity, [people[0].pk], True, "email")
    assert certificate_of(people[0]).certificate_number != number


def test_undo_is_refused_once_approved(generated, director) -> None:
    """A certificate approved before this change without an evaluation (old rows are kept as they are)."""
    activity, people = generated
    Certificate.objects.filter(registration=people[0]).update(status=S.APPROVED, approved_by=director)
    with pytest.raises(registration_services.RegistrationError, match="certificate was already approved"):
        registration_services.set_attendance(director, activity, [people[0].pk], False)
    reg = Registration.objects.select_related("evaluation_submission").prefetch_related("certificates").get(pk=people[1].pk)
    assert registration_services.undo_refused_reason(reg) is None  # still waiting: undo allowed


def test_undo_is_refused_after_the_evaluation(generated, director, evaluate) -> None:
    activity, people = generated
    evaluate(people[0])
    with pytest.raises(registration_services.RegistrationError, match="already submitted the evaluation"):
        registration_services.set_attendance(director, activity, [people[0].pk], False)
    assert Certificate.objects.filter(registration=people[0]).exists()


# --- B2: ready for approval (computed) ----------------------------------------------------------------------------


def test_readiness_labels(generated, design, evaluate) -> None:
    activity, people = generated
    zero = certificate_of(people[0])
    assert services.readiness(zero) == services.WAITING_FOR_EVALUATION
    assert services.readiness(appreciation_of(activity)) is None  # resource person: signatures only
    evaluate(people[0])
    assert services.readiness(certificate_of(people[0])) is None
    without_image(design[1])
    assert services.readiness(certificate_of(people[0])) == services.WAITING_FOR_SIGNATURES
    assert services.readiness(appreciation_of(activity)) == services.WAITING_FOR_SIGNATURES


def test_after_the_evaluation_deadline_it_never_becomes_ready(generated) -> None:
    activity, people = generated
    after = activity.evaluation_deadline + timedelta(minutes=1)
    assert services.readiness(certificate_of(people[1]), now=after) == services.EVALUATION_CLOSED


def test_readiness_map_matches_one_by_one(generated, evaluate, django_assert_max_num_queries) -> None:
    activity, people = generated
    evaluate(people[0])
    certificates = list(Certificate.objects.filter(activity=activity).select_related("activity", "right_signatory", "left_signatory"))
    with django_assert_max_num_queries(1):
        labels = services.readiness_map(certificates)
    assert labels == {c.pk: services.readiness(c) for c in certificates}


# --- B3: approve one / all ready ---------------------------------------------------------------------------------


def test_approve_one_needs_it_to_be_ready(generated, director, evaluate) -> None:
    activity, people = generated
    with pytest.raises(services.CertificateError, match="Waiting for evaluation"):
        services.approve_certificate(director, certificate_of(people[0]))
    evaluate(people[0])
    assert services.approve_certificate(director, certificate_of(people[0])).status == S.APPROVED


def test_approve_all_ready_leaves_the_others_waiting(generated, director, evaluate) -> None:
    activity, people = generated
    evaluate(people[0])
    assert len(services.ready_certificates(activity)) == 2  # one participant + the resource person
    assert services.approve_certificates(director, activity=activity) == 2
    assert certificate_of(people[1]).status == S.GENERATED  # not evaluated: keeps waiting
    with pytest.raises(services.CertificateError, match=services.NOTHING_READY):
        services.approve_certificates(director, activity=activity)


def test_approve_all_ready_names_the_missing_signature(generated, director, design) -> None:
    activity, _ = generated
    without_image(design[1])
    assert services.approval_closed_reason(activity) == "Add the signature of Dr. Vice Pres (Vice President) first."
    with pytest.raises(services.CertificateError, match=r"Add the signature of Dr\. Vice Pres"):
        services.approve_certificates(director, activity=activity)


def test_admin_staff_cannot_approve(generated, admin_staff, evaluate) -> None:
    from django.core.exceptions import PermissionDenied

    activity, people = generated
    evaluate(people[0])
    with pytest.raises(PermissionDenied):
        services.approve_certificate(admin_staff, certificate_of(people[0]))


def test_approved_participant_is_issued_and_emailed(generated, director, evaluate, run_tasks, mailoutbox,
                                                    django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    evaluate(people[0], email="zero@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_certificate(director, certificate_of(people[0]))
    certificate = certificate_of(people[0])
    assert certificate.status == S.ISSUED and certificate.sent_at is not None
    assert len([m for m in mailoutbox if m.to == ["zero@example.com"]]) == 1  # sent once, to certificate_email


# --- B5: counts include only ready certificates --------------------------------------------------------------------


def test_s5_and_attention_count_only_ready_certificates(generated, evaluate) -> None:
    activity, people = generated
    counts = metrics.certificates_now(NOW_FILTERS)
    assert (counts["waiting_activities"], counts["waiting_certificates"]) == (1, 1)  # the resource person only
    texts = [item.text for item in attention.activity_attention(activity)]
    assert "1 certificate ready for approval." in texts
    evaluate(people[0])
    counts = metrics.certificates_now(NOW_FILTERS)
    assert counts["waiting_certificates"] == 2
    assert metrics.certificates_waiting_by_activity(NOW_FILTERS) == [
        {"activity_id": activity.pk, "title": activity.title, "count": 2}]
    assert "2 certificates ready for approval." in [item.text for item in attention.activity_attention(activity)]


def test_nothing_ready_is_not_a_staff_task(generated, director) -> None:
    activity, people = generated
    services.approve_certificate(director, appreciation_of(activity))  # only participants left, not evaluated
    assert metrics.certificates_now(NOW_FILTERS)["waiting_certificates"] == 0
    texts = [item.text for item in attention.activity_attention(activity)]
    assert not [text for text in texts if "ready for approval" in text or "waiting for approval" in text]


def test_attention_parity_with_the_list_count(generated, evaluate) -> None:
    activity, people = generated
    evaluate(people[0])
    counts = attention.attention_counts([activity])
    assert counts[activity.pk] == len(attention.activity_attention(activity))


def test_dashboard_says_ready_for_your_approval(generated, evaluate, director) -> None:
    from django.test import Client

    activity, people = generated
    evaluate(people[0])
    client = Client()
    client.force_login(director)
    page = client.get(reverse("home")).context["page"]
    item = next(item for item in page.attention if "ready for" in item.title)
    assert item.title == "1 activity (2 certificates) ready for your approval" and item.action_label == "Approve"
