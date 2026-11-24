"""One approval per ACTIVITY (ASSUMPTIONS 369): after "Approve certificates for this activity" every certificate that
becomes ready is approved and emailed by itself (later evaluations, certificates created later, the 5-minute job); a
change to what is printed (signatory, title, a new version of a person, "Apply the current design", the certificate
text) cancels the approval; Issued certificates never change. The tab, the counts (S5, Needs attention, dashboard),
the URLs and the permissions."""

from datetime import date

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from activities import attention
from activities import services as activity_services
from activities.models import Activity, ActivityAuditLog
from certificates import services
from certificates.models import Certificate
from registrations import services as registration_services
from registrations.models import Registration
from reports import metrics

from .conftest import S, appreciation_of, certificate_of, template_upload, upload
from conftest import image_bytes

pytestmark = pytest.mark.django_db

NOW_FILTERS = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))


def approval_history(activity) -> list[tuple]:
    return list(ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificate approval")
                .order_by("pk").values_list("changed_by", "old_value", "new_value"))


def approved(activity) -> bool:
    return services.is_approved(Activity.objects.get(pk=activity.pk))


@pytest.fixture
def approved_early(generated, director, run_tasks, django_capture_on_commit_callbacks):
    """Approved before anyone evaluated (only the resource person was ready)."""
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_activity_certificates(director, activity)
    return activity, people


# --- Automatic approval after the activity's approval ---------------------------------------------------------------


def test_each_later_evaluation_is_approved_and_emailed_automatically(approved_early, director, evaluate,
                                                                     django_capture_on_commit_callbacks) -> None:
    activity, people = approved_early
    assert mail.outbox == []
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0], name="Ana Cruz", email="ana.first@example.com")
    first = certificate_of(people[0])
    assert first.status == S.ISSUED and first.sent_at and first.approved_by == director
    assert [m.to for m in mail.outbox] == [["ana.first@example.com"]]
    assert certificate_of(people[1]).status == S.GENERATED  # not evaluated yet
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[1], name="Ben Reyes", email="ben.second@example.com")
    assert certificate_of(people[1]).status == S.ISSUED
    assert [m.to for m in mail.outbox] == [["ana.first@example.com"], ["ben.second@example.com"]]  # no extra click


def test_resource_persons_are_approved_and_ready_to_print(approved_early, director) -> None:
    activity, _ = approved_early
    speaker = appreciation_of(activity)
    assert speaker.status == S.APPROVED and speaker.file_path  # rendered: ready to print
    assert services.mark_handed_over(director, activity=activity) == 1


def test_a_certificate_created_later_follows_the_same_rule(generated, director, run_tasks,
                                                           django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    services.approve_activity_certificates(director, activity)
    from activities.models import ActivityResourcePerson

    speaker = activity_services.create_resource_person(director, "Dr. Late Speaker", "late@example.com")
    ActivityResourcePerson.objects.create(activity=activity, resource_person=speaker, topic="Late topic")  # (read-only form)
    with django_capture_on_commit_callbacks(execute=True):
        services.generate_missing_certificates(activity)
    late = Certificate.objects.get(activity=activity, resource_person=speaker)
    assert late.status == S.APPROVED and late.approved_by == director


def test_attendance_marked_later_is_approved_after_its_evaluation(generated, director, evaluate, run_tasks,
                                                                  django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    services.approve_activity_certificates(director, activity)
    late = Registration.objects.create(activity=activity, template=people[0].template,
                                       participant_category=people[0].participant_category, name="Late Comer",
                                       email="late.comer@example.com")
    registration_services.set_attendance(director, activity, [late.pk], True, "staff")
    assert certificate_of(late).status == S.GENERATED  # waits for the evaluation
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(late, name="Late Comer", email="late.cert@example.com")
    assert certificate_of(late).status == S.ISSUED


def test_nothing_is_approved_automatically_without_the_activity_approval(generated, evaluate,
                                                                         django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
    assert certificate_of(people[0]).status == S.GENERATED
    assert services.approve_pending_certificates() == 0
    assert services.approve_ready_certificates(activity) == []


def test_the_five_minute_job_catches_up(generated, director, evaluate, run_tasks) -> None:
    activity, people = generated
    services.approve_activity_certificates(director, activity)
    evaluate(people[0])  # its after-commit call never ran (no capture): the job catches it up
    assert certificate_of(people[0]).status == S.GENERATED
    assert services.approve_pending_certificates() == 1
    assert certificate_of(people[0]).status == S.APPROVED
    assert services.approve_pending_certificates() == 0  # safe to run again


def test_the_automation_task_runs_the_catch_up(generated, director, evaluate, run_tasks) -> None:
    from activities.tasks import run_activity_automation

    activity, people = generated
    services.approve_activity_certificates(director, activity)
    evaluate(people[0])
    assert run_activity_automation()["certificates_approved"] == 1


# --- Cancelling the approval -------------------------------------------------------------------------------------


def test_changing_a_signatory_cancels_the_approval(approved_early, director, admin_member) -> None:
    activity, _ = approved_early
    other = services.add_signatory(director, "Dr. Other", "Chancellor", upload("o.png", image_bytes()))
    result = services.set_activity_signatory(director, activity, "right", other)
    assert result["cancelled"] == 1
    assert not approved(activity)
    assert appreciation_of(activity).status == S.GENERATED and not appreciation_of(activity).file_path
    assert approval_history(activity)[-1] == (director.pk, f"Approved by {director.name}",
                                              "Needs approval (cancelled: right signatory changed)")


def test_changing_only_a_title_cancels_the_approval(approved_early, director, design) -> None:
    activity, _ = approved_early
    _, vp, _ = design
    services.set_activity_signatory(director, activity, "right", vp, title="Vice President, CSU")
    assert not approved(activity)
    assert approval_history(activity)[-1][2] == "Needs approval (cancelled: right title changed)"


def test_a_new_version_of_a_person_cancels_every_activity_using_them(approved_early, director, design) -> None:
    activity, _ = approved_early
    _, vp, _ = design
    result = services.replace_signature(director, vp, upload("new.png", image_bytes()))
    assert result["cancelled"] == 1
    assert not approved(activity)
    assert approval_history(activity)[-1][2].startswith("Needs approval (cancelled: new version of Dr. Vice Pres")


def test_apply_the_current_design_cancels_the_approval(approved_early, director, admin_member) -> None:
    activity, _ = approved_early
    services.upload_template(admin_member, "New design", template_upload("n.png"), activate=True)
    services.regenerate_certificates(director, activity=activity)
    assert not approved(activity)
    assert approval_history(activity)[-1][2] == "Needs approval (cancelled: current design applied)"


def test_issued_certificates_never_change_when_the_approval_is_cancelled(approved_early, director, admin_member,
                                                                       evaluate,
                                                                       django_capture_on_commit_callbacks) -> None:
    activity, people = approved_early
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
    issued = certificate_of(people[0])
    assert issued.status == S.ISSUED
    other = services.add_signatory(director, "Dr. Other", "Chancellor", upload("o.png", image_bytes()))
    services.set_activity_signatory(director, activity, "right", other)
    again = certificate_of(people[0])
    assert again.status == S.ISSUED and again.right_signatory_id == issued.right_signatory_id
    assert again.file_path.name == issued.file_path.name and again.sent_at == issued.sent_at


def test_after_cancelling_a_later_evaluation_waits_for_approval(approved_early, director, admin_member, evaluate,
                                                                django_capture_on_commit_callbacks) -> None:
    activity, people = approved_early
    other = services.add_signatory(director, "Dr. Other", "Chancellor", upload("o.png", image_bytes()))
    services.set_activity_signatory(director, activity, "right", other)
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
    assert certificate_of(people[0]).status == S.GENERATED and mail.outbox == []
    approved_now = services.approve_activity_certificates(director, activity)
    assert {c.pk for c in approved_now} == {certificate_of(people[0]).pk, appreciation_of(activity).pk}


def test_no_change_cancels_nothing(approved_early, director, design) -> None:
    activity, _ = approved_early
    _, vp, _ = design
    assert services.set_activity_signatory(director, activity, "right", vp)["changed"] is False
    assert approved(activity)


# --- Counts: S5, Needs attention, dashboard ---------------------------------------------------------------------


def test_counts_are_activities_that_need_approval(generated, director) -> None:
    activity, _ = generated
    counts = metrics.certificates_now(NOW_FILTERS)
    assert (counts["waiting_activities"], counts["waiting_certificates"]) == (1, 3)  # not only the ready ones
    assert "Certificates need approval." in [item.text for item in attention.activity_attention(activity)]
    services.approve_activity_certificates(director, activity)
    counts = metrics.certificates_now(NOW_FILTERS)
    assert (counts["waiting_activities"], counts["waiting_certificates"]) == (0, 0)  # the others wait for evaluations
    assert "Certificates need approval." not in [item.text for item in attention.activity_attention(activity)]


def test_a_missing_signature_shows_instead_of_needs_approval(generated, design) -> None:
    from certificates.models import CertificateSignatory

    activity, _ = generated
    CertificateSignatory.objects.filter(pk=design[1].pk).update(signature_image_path=None)
    texts = [item.text for item in attention.activity_attention(activity)]
    assert "Add the signature of Dr. Vice Pres (Vice President) first." in texts
    assert "Certificates need approval." not in texts


def test_attention_parity_with_the_list_count(generated) -> None:
    activity, _ = generated
    assert attention.attention_counts([activity])[activity.pk] == len(attention.activity_attention(activity))


def test_dashboard_counts_activities(generated, director) -> None:
    client = Client()
    client.force_login(director)
    page = client.get(reverse("home")).context["page"]
    item = next(item for item in page.attention if "certificate approval" in item.title)
    assert item.title == "1 activity needs certificate approval" and item.action_label == "Approve"


# --- The tab, the URLs, permissions -----------------------------------------------------------------------------


def tab(client, activity):
    return client.get(reverse("activities:detail", args=[activity.pk]) + "?tab=certificates")


def test_the_tab_shows_the_state_and_one_approve_button(generated, director) -> None:
    activity, _ = generated
    client = Client()
    client.force_login(director)
    html = tab(client, activity).content.decode()
    assert "Needs approval" in html and "Approve certificates for this activity" in html
    assert "Approve all ready certificates" not in html and ">Approve<" not in html
    assert "as soon as they submit their evaluation" in html  # the confirm dialog's wording
    response = client.post(reverse("activities:certificates_approve_activity", args=[activity.pk]))
    assert response.status_code == 302
    html = tab(client, activity).content.decode()
    assert f"Approved by {director.name} on " in html
    assert "Approve certificates for this activity" not in html


def test_view_only_members_see_the_state_but_no_button(generated, view_only) -> None:
    activity, _ = generated
    client = Client()
    client.force_login(view_only)
    html = tab(client, activity).content.decode()
    assert "Needs approval" in html and "Approve certificates for this activity" not in html
    response = client.post(reverse("activities:certificates_approve_activity", args=[activity.pk]))
    assert response.status_code == 403
    assert not approved(activity)


def test_the_old_per_certificate_urls_are_gone(generated, director) -> None:
    activity, people = generated
    client = Client()
    client.force_login(director)
    certificate = certificate_of(people[0])
    assert client.post(f"/activities/{activity.pk}/certificates/{certificate.pk}/approve/").status_code == 404
    assert client.post(f"/activities/{activity.pk}/certificates/approve-ready/").status_code == 404


def test_a_refused_approval_is_one_message(generated, director, design) -> None:
    from certificates.models import CertificateSignatory

    activity, _ = generated
    CertificateSignatory.objects.filter(pk=design[1].pk).update(signature_image_path=None)
    client = Client()
    client.force_login(director)
    response = client.post(reverse("activities:certificates_approve_activity", args=[activity.pk]), follow=True)
    assert "Not approved: Add the signature of Dr. Vice Pres" in response.content.decode()
    assert not approved(activity)


def test_the_api_approves_per_activity_only(generated, director) -> None:
    activity, people = generated
    client = Client()
    client.force_login(director)
    url = reverse("certificates:api_action", args=["approve"])
    response = client.post(url, {"certificate_ids": [certificate_of(people[0]).pk]}, content_type="application/json")
    assert response.status_code == 400
    response = client.post(url, {"activity_id": activity.pk}, content_type="application/json")
    assert response.status_code == 200 and response.json()["changed"] == 1
    assert approved(activity)
