"""Only the TBI Director approves certificates (ASSUMPTIONS 421, NAVIGATU UAT): the seed defaults (create-only), what
needs can_approve_certificates (approve, reissue, the default text: 403 for the Project Heads, Admin Staff and the
System Administrator), what stays with can_create_activity (Retry, signatories, prepare, custom text, thumbnails), and
the approvers' notifications ("ready for your review", "waiting for your approval", once per activity)."""

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from accounts.seed import load_lookups
from certificates import pages, services
from certificates.models import CertificateText
from notifications.models import Notification
from notifications.pages import target_label, target_url
from notifications.services import certificate_reviewers

from .conftest import appreciation_of

pytestmark = pytest.mark.django_db

P = CertificateText.CertificateType.PARTICIPATION
OTHERS = ["Incubation Project Head", "Community Development Head", "Admin Staff", Role.SYSTEM_ADMINISTRATOR]
CREATORS = ["Incubation Project Head", "Community Development Head", "Admin Staff"]


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def waiting(activity=None):
    rows = Notification.objects.filter(type=Notification.CERTIFICATES_WAITING)
    return rows.filter(activity=activity) if activity is not None else rows


# --- Seed defaults --------------------------------------------------------------------------------------------------


def test_only_the_director_approves_by_default(roles) -> None:
    assert roles[Role.DIRECTOR].can_approve_certificates
    for name in OTHERS:
        assert not roles[name].can_approve_certificates, name


def test_the_seed_never_overwrites_an_admin_console_choice(roles) -> None:
    Role.objects.filter(role_name="Admin Staff").update(can_approve_certificates=True)
    Role.objects.filter(role_name=Role.DIRECTOR).update(can_approve_certificates=False)
    load_lookups()
    assert Role.objects.get(role_name="Admin Staff").can_approve_certificates
    assert not Role.objects.get(role_name=Role.DIRECTOR).can_approve_certificates


# --- Director only: approve, reissue, the default text ---------------------------------------------------------------


def test_the_director_approves(generated, director) -> None:
    activity, _ = generated
    response = client_for(director).post(reverse("activities:certificates_approve_activity", args=[activity.pk]))
    assert response.status_code == 302
    activity.refresh_from_db()
    assert services.is_approved(activity) and activity.certificates_approved_by == director


@pytest.mark.parametrize("role_name", OTHERS)
def test_other_roles_cannot_approve_reissue_or_edit_the_default_text(generated, make_member, role_name) -> None:
    activity, _ = generated
    member = make_member(role_name)
    client = client_for(member)
    assert client.post(reverse("activities:certificates_approve_activity", args=[activity.pk])).status_code == 403
    assert client.post(reverse("activities:certificates_reissue_all", args=[activity.pk])).status_code == 403
    assert client.get(reverse("certificates:text")).status_code == 403
    assert client.post(reverse("certificates:text_save", args=[P]), {}).status_code == 403
    with pytest.raises(PermissionDenied):
        services.approve_activity_certificates(member, activity)
    with pytest.raises(PermissionDenied):
        services.reissue_certificates(member, activity=activity)
    with pytest.raises(PermissionDenied):
        services.save_default_text(member, P, services.text_values(services.current_texts(None)[P]))
    activity.refresh_from_db()
    assert not services.is_approved(activity)
    html = client.get(pages.tab_url(activity.pk)).content.decode()
    assert "Needs approval" in html and "Approve certificates for this activity" not in html


# --- Kept for staff who create activities ----------------------------------------------------------------------------


@pytest.mark.parametrize("role_name", CREATORS)
def test_creators_keep_signatories_prepare_custom_text_and_thumbnails(new_activity, design, make_member,
                                                                      role_name) -> None:
    _, vp, head = design
    member = make_member(role_name)
    services.set_activity_signatory(member, new_activity, "right", vp, title="Vice President, CSU")
    values = services.text_values(services.current_texts(new_activity)[P])
    services.save_activity_text(member, new_activity, {P: {**values, "title": "Certificate of Attendance"}})
    services.prepare_activity_certificates(member, new_activity)
    client = client_for(member)
    assert client.get(reverse("certificates:signature_image", args=[vp.pk])).status_code == 200
    html = client.get(pages.tab_url(new_activity.pk)).content.decode()
    assert reverse("certificates:signature_image", args=[vp.pk]) in html  # the thumbnail
    assert "Approve certificates for this activity" not in html  # prepared, but only the Director approves


@pytest.mark.parametrize("role_name", CREATORS)
def test_creators_can_retry_a_failed_pdf(generated, director, make_member, role_name) -> None:
    activity, _ = generated
    speaker = appreciation_of(activity)
    services.approve_activity_certificates(director, activity)  # only the resource person is ready
    services.record_render_failure(speaker.pk, RuntimeError("boom"))
    client = client_for(make_member(role_name))
    html = client.get(pages.tab_url(activity.pk)).content.decode()
    assert f'form="retry-{speaker.pk}"' in html
    response = client.post(reverse("activities:certificates_action", args=[activity.pk]),
                           {"action": "retry_pdf", "certificate_ids": [speaker.pk]})
    assert response.status_code == 302
    speaker.refresh_from_db()
    assert speaker.render_failed_at is None


def test_the_system_administrator_cannot_retry_but_still_sees_thumbnails(generated, director, design,
                                                                        admin_member) -> None:
    activity, _ = generated
    speaker = appreciation_of(activity)
    services.approve_activity_certificates(director, activity)
    services.record_render_failure(speaker.pk, RuntimeError("boom"))
    client = client_for(admin_member)
    response = client.post(reverse("activities:certificates_action", args=[activity.pk]),
                           {"action": "retry_pdf", "certificate_ids": [speaker.pk]})
    assert response.status_code == 403
    assert client.get(reverse("certificates:signature_image", args=[design[1].pk])).status_code == 200


def test_view_only_still_sees_no_signature_images(design, view_only) -> None:
    assert client_for(view_only).get(reverse("certificates:signature_image", args=[design[1].pk])).status_code == 403


# --- Notifications ---------------------------------------------------------------------------------------------------


def test_ready_for_review_goes_to_the_approvers_only(new_activity, design, director, make_member) -> None:
    staff = make_member("Admin Staff")
    head = make_member("Incubation Project Head")
    services.prepare_activity_certificates(staff, new_activity)
    rows = Notification.objects.filter(type=Notification.CERTIFICATES_PREPARED)
    assert list(rows.values_list("member", flat=True)) == [director.pk]
    # The flag alone decides (no need to manage signatories): a role switched on in the Admin Console is told too.
    Role.objects.filter(role_name="Incubation Project Head").update(can_approve_certificates=True)
    assert head in certificate_reviewers()


def test_waiting_for_approval_is_sent_once_when_certificates_are_first_created(conducted, design, director,
                                                                             make_member, roles) -> None:
    activity, people = conducted
    make_member("Admin Staff")  # not an approver: not told
    services.generate_missing_certificates(activity)
    rows = list(waiting(activity).values_list("member", "message"))
    assert rows == [(director.pk, f"Certificates of {activity.title} are waiting for your approval")]
    note = waiting(activity).get()
    assert target_url(note).endswith("?tab=certificates") and target_label(note) == "Open certificates"
    # More certificates later (attendance marked after Conducted) or another run: no second notification.
    services.generate_missing_certificates(activity)
    assert waiting(activity).count() == 1


def test_no_waiting_notification_after_an_early_approval(attended, design, director, make_member) -> None:
    from activities import services as activity_services

    people = attended(1)
    activity = people[0].activity
    services.prepare_activity_certificates(make_member("Admin Staff"), activity)
    services.approve_activity_certificates(director, activity)
    activity_services.change_status(director, activity, "Conducted")
    assert activity.certificates.exists() and not waiting().exists()


def test_the_amber_warning_when_nobody_can_approve_stays(roles, admin_member, director) -> None:
    Role.objects.filter(role_name=Role.DIRECTOR).update(can_approve_certificates=False)
    html = client_for(admin_member).get(reverse("accounts:console_roles")).content.decode()
    assert "No active member can approve certificates." in html
