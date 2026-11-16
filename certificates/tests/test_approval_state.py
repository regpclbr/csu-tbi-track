"""ASSUMPTIONS 373: the approval state says what the user needs to do. Not approved AND certificates waiting = "Needs
approval" (orange, counted, the button); every certificate Issued = "All certificates issued (N)" (green, no button);
no certificates = "Certificates are created automatically …" (no button); otherwise the grey "Not approved — new
certificates will wait for approval"; approved = "Approved by … on …". Only "Needs approval" is ever counted. One rule
(services.approval_state) for the Certificates page header and the tab's card; the counts (S5, Needs attention, the Activities list, the dashboard) keep
services.needs_approval_q, and the two agree.

The owner's case: activity "Test" had its two certificates approved one by one under the old rule (item 362), issued
and emailed; the per-activity approval (item 369) came later and marked no activity approved, so it is not approved
and nothing waits."""

from datetime import date

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities import attention
from activities.models import Activity
from certificates import services
from certificates.models import Certificate
from reports import metrics

from .conftest import S

pytestmark = pytest.mark.django_db

NOW_FILTERS = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))
GREY = "Not approved — new certificates will wait for approval"
ALL_ISSUED_2 = "All certificates issued (2)"
NO_CERTIFICATES = "Certificates are created automatically when the activity is Conducted."


def all_issued_not_approved(activity: Activity) -> Activity:
    """The "Test" activity's state: every certificate Issued (and sent), the activity never approved."""
    Certificate.objects.filter(activity=activity).update(status=S.ISSUED, issued_at=timezone.now(), sent_at=timezone.now())
    activity.refresh_from_db()
    assert not services.is_approved(activity)
    return activity


def tab_html(member, activity) -> str:
    client = Client()
    client.force_login(member)
    return client.get(reverse("activities:detail", args=[activity.pk]) + "?tab=certificates").content.decode()


def counted_by_needs_approval_q(activity) -> bool:
    return Certificate.objects.filter(services.needs_approval_q(), activity=activity).exists()


# --- The rule -------------------------------------------------------------------------------------------------------


def counts(generated: int = 0, approved: int = 0, issued: int = 0) -> dict:
    return {S.GENERATED: generated, S.APPROVED: approved, S.ISSUED: issued}


def test_five_states(generated, director) -> None:
    activity, _ = generated
    state = services.approval_state(activity, counts(generated=3))
    assert (state.kind, state.text, state.offers_approval) == (services.NEEDS_APPROVAL_KIND, "Needs approval", True)
    state = services.approval_state(activity, counts(generated=1, issued=2))  # a late certificate: back to a task
    assert state.kind == services.NEEDS_APPROVAL_KIND and state.offers_approval
    state = services.approval_state(activity, counts(issued=2))
    assert (state.kind, state.text, state.note, state.offers_approval) == (
        services.ALL_ISSUED_KIND, ALL_ISSUED_2, services.ALL_ISSUED_NOTE, False)
    state = services.approval_state(activity, counts())
    assert (state.kind, state.text, state.note, state.offers_approval) == (
        services.NO_CERTIFICATES_KIND, NO_CERTIFICATES, "", False)
    assert services.approval_state(activity, {}).kind == services.NO_CERTIFICATES_KIND
    state = services.approval_state(activity, counts(approved=1, issued=1))  # old data: approving is still possible
    assert (state.kind, state.text, state.offers_approval) == (services.NOT_APPROVED_KIND, GREY, True)
    services.approve_activity_certificates(director, activity)
    activity.refresh_from_db()
    for some in (counts(), counts(generated=2), counts(issued=3)):  # approved wins whatever the certificates are
        state = services.approval_state(activity, some)
        assert state.kind == services.APPROVED_KIND and state.text.startswith(f"Approved by {director.name} on ")


def test_approval_counts_cover_every_status_and_activity(generated) -> None:
    activity, _ = generated
    assert services.approval_counts([activity.pk]) == {activity.pk: counts(generated=3)}
    Certificate.objects.filter(pk=Certificate.objects.filter(activity=activity).first().pk).update(status=S.ISSUED)
    assert services.approval_counts([activity.pk, 999999]) == {activity.pk: counts(generated=2, issued=1),
                                                               999999: counts()}
    assert services.approval_counts([]) == {}


def test_the_state_agrees_with_the_counting_rule(generated, director) -> None:
    """NEEDS_APPROVAL_KIND exactly when needs_approval_q counts the activity: waiting + not approved, all issued,
    approved."""
    activity, _ = generated

    def kind() -> str:
        activity.refresh_from_db()
        return services.approval_state(activity, services.approval_counts([activity.pk])[activity.pk]).kind

    assert kind() == services.NEEDS_APPROVAL_KIND and counted_by_needs_approval_q(activity)
    all_issued_not_approved(activity)
    assert kind() == services.ALL_ISSUED_KIND and not counted_by_needs_approval_q(activity)
    Certificate.objects.filter(activity=activity).update(status=S.GENERATED, issued_at=None, sent_at=None)
    services.approve_activity_certificates(director, activity)
    assert kind() == services.APPROVED_KIND and not counted_by_needs_approval_q(activity)


# --- The tab's "Certificate approval" card --------------------------------------------------------------------------


def test_tab_all_issued_shows_the_count_and_no_button(generated, director, view_only) -> None:
    activity = all_issued_not_approved(generated[0])
    for member in (director, view_only):
        html = tab_html(member, activity)
        assert 'data-approval-state="all_issued"' in html and "All certificates issued (3)" in html
        assert services.ALL_ISSUED_NOTE in html and "bg-green-100 text-green-800" in html
        assert "Approve certificates for this activity" not in html  # hidden, not disabled
        assert services.ALL_ISSUED not in html and 'id="approval-reason"' not in html
        assert "Needs approval" not in html and GREY not in html and "No certificate is waiting now." not in html


def test_tab_with_waiting_certificates_still_needs_approval(generated, director, view_only) -> None:
    activity, _ = generated
    html = tab_html(director, activity)
    assert "Needs approval" in html and 'data-approval-state="needs_approval"' in html
    assert "Approve certificates for this activity" in html and "All certificates issued" not in html
    staff = tab_html(view_only, activity)
    assert "Needs approval" in staff and "Approve certificates for this activity" not in staff


def test_tab_without_certificates_says_only_how_they_are_created(conducted, director, view_only) -> None:
    activity, _ = conducted
    assert not Certificate.objects.filter(activity=activity).exists()
    for member in (director, view_only):
        html = tab_html(member, activity)
        assert 'data-approval-state="no_certificates"' in html and NO_CERTIFICATES in html
        assert "Approve certificates for this activity" not in html and 'id="approval-reason"' not in html
        assert services.NOTHING_TO_APPROVE not in html
        assert "Needs approval" not in html and GREY not in html
        assert "then they are approved here once" not in html  # the old, longer sentence


def test_the_service_still_refuses_when_nothing_can_be_approved(generated, director) -> None:
    """The button is hidden, but a request made by hand is still refused by the service."""
    activity = all_issued_not_approved(generated[0])
    with pytest.raises(services.CertificateError, match="already issued"):
        services.approve_activity_certificates(director, activity)
    client = Client()
    client.force_login(director)
    client.post(reverse("activities:certificates_approve_activity", args=[activity.pk]))
    activity.refresh_from_db()
    assert not services.is_approved(activity)


# --- The counts never include an activity with nothing waiting ------------------------------------------------------


def test_not_approved_with_nothing_waiting_is_counted_nowhere(generated, director) -> None:
    activity = all_issued_not_approved(generated[0])
    counts = metrics.certificates_now(NOW_FILTERS)
    assert (counts["waiting_activities"], counts["waiting_certificates"]) == (0, 0)  # S5
    assert metrics.certificates_waiting_by_activity(NOW_FILTERS) == []
    texts = [item.text for item in attention.activity_attention(activity)]
    assert "Certificates need approval." not in texts
    assert attention.attention_counts([activity])[activity.pk] == len(texts)  # the Activities list agrees
    client = Client()
    client.force_login(director)
    page = client.get(reverse("home")).context["page"]
    assert not [item for item in page.attention if "certificate approval" in item.title]


def test_a_new_waiting_certificate_makes_it_need_approval_again(generated, director) -> None:
    """A certificate created later (attendance marked after Conducted) waits: the activity is counted again."""
    activity = all_issued_not_approved(generated[0])
    assert "All certificates issued (3)" in tab_html(director, activity)
    Certificate.objects.filter(pk=Certificate.objects.filter(activity=activity).first().pk).update(
        status=S.GENERATED, issued_at=None, sent_at=None)
    assert metrics.certificates_now(NOW_FILTERS)["waiting_activities"] == 1
    assert "Certificates need approval." in [item.text for item in attention.activity_attention(activity)]
    html = tab_html(director, activity)
    assert "Needs approval" in html and "Approve certificates for this activity" in html
    assert "All certificates issued" not in html
