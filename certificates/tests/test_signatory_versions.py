"""Inactive signatories on an activity (ASSUMPTIONS 367): versions of a person are linked (previous_version), a new
version moves every activity on ANY older version, "Use the current version", the amber note, the Needs attention
line, and the data migration 0010 (titles filled, versions linked, no activity changed)."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities import attention
from activities import services as activity_services
from certificates import services
from certificates.models import ActivitySignatory, Certificate, CertificateSignatory
from certificates.pages import tab_url

from .conftest import S, certificate_of, upload
from conftest import image_bytes

pytestmark = pytest.mark.django_db


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def png():
    return upload("sig.png", image_bytes(40, 20))


def stale(activity, slot: str, signatory: CertificateSignatory) -> None:
    """Point a slot at an older version directly (what migration 0007 left on 12 activities)."""
    ActivitySignatory.objects.filter(activity=activity, slot=slot).update(signatory=signatory)
    Certificate.objects.filter(activity=activity).exclude(status=S.ISSUED).update(**{f"{slot}_signatory": signatory})


@pytest.fixture
def versions(generated, director, valid_data):
    """The Director replaced their image once: v1 (inactive) -> v2 (active). `activity` was left on v1."""
    activity, people = generated
    v1 = services.activity_signatories(activity)["left"]
    v2 = services.upload_signature(director, activity, "left", png())["signatory"]
    stale(activity, "left", v1)
    v1.refresh_from_db()
    return activity, people, v1, v2


def test_a_new_version_remembers_the_one_it_replaced(versions) -> None:
    _, _, v1, v2 = versions
    assert v2.previous_version == v1 and services.older_versions(v2) == [v1.pk]
    assert services.current_version_of(v1) == v2 and services.current_version_of(v2) == v2


def test_a_new_version_moves_activities_on_any_older_version(versions, director, valid_data) -> None:
    activity, people, v1, v2 = versions
    other = activity_services.create_activity(director, valid_data(title="On v2"))
    assert services.activity_signatories(other)["left"] == v2
    assert services.version_impact(v2)["activities"] == 2  # both activities are counted before saving

    v3 = services.replace_signature(director, v2, png())["signatory"]

    assert services.activity_signatories(activity)["left"] == v3  # was on v1, not on the replaced v2
    assert services.activity_signatories(other)["left"] == v3
    assert certificate_of(people[1]).left_signatory == v3  # its not-issued certificates follow
    assert services.older_versions(v3) == [v2.pk, v1.pk]


def test_use_the_current_version(versions, director) -> None:
    activity, people, v1, v2 = versions
    services.set_activity_signatory(director, activity, "left", v1, title="Director, CSU-TBI")  # same person: allowed
    response = client_for(director).post(reverse("activities:certificates_signatory_current", args=[activity.pk, "left"]))
    assert response.status_code == 302
    row = services.activity_slots(activity)["left"]
    assert row.signatory == v2 and row.position_on_certificate == "Director, CSU-TBI"  # the title is kept
    assert certificate_of(people[1]).left_signatory == v2  # refreshed like any slot change
    assert services.inactive_slots(activity) == {}


def test_use_the_current_version_is_refused_without_a_newer_active_version(generated, director) -> None:
    activity, _ = generated
    head = services.activity_signatories(activity)["left"]
    CertificateSignatory.objects.filter(pk=head.pk).update(is_active=False, default_slot=None)
    with pytest.raises(services.CertificateError, match="no newer active version"):
        services.use_current_version(director, activity, "left")
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "is inactive. Choose someone else" in html and "Use the current version" not in html


def test_the_tab_shows_the_amber_note_with_the_button(versions, director, admin_staff) -> None:
    activity, _, v1, v2 = versions
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "older, inactive version" in html and "Use the current version" in html
    viewer = client_for(admin_staff).get(tab_url(activity.pk)).content.decode()
    assert "older, inactive version" in viewer and "Use the current version" not in viewer  # view only
    assert client_for(admin_staff).post(
        reverse("activities:certificates_signatory_current", args=[activity.pk, "left"])).status_code == 403


def test_needs_attention_says_a_signatory_is_inactive_unless_everything_is_issued(versions) -> None:
    activity, _, _, _ = versions
    item = next(i for i in attention.activity_attention(activity) if i.text == "A signatory on this activity is inactive.")
    assert not item.informational and item.tab == "certificates"
    Certificate.objects.filter(activity=activity).update(status=S.ISSUED, issued_at=timezone.now())
    assert "A signatory on this activity is inactive." not in [i.text for i in attention.activity_attention(activity)]


def test_needs_attention_for_an_activity_without_certificates(director, design, valid_data) -> None:
    activity = activity_services.create_activity(director, valid_data())
    _, _, head = design
    CertificateSignatory.objects.filter(pk=head.pk).update(is_active=False, default_slot=None)
    assert "A signatory on this activity is inactive." in [i.text for i in attention.activity_attention(activity)]


# --- Migration 0010 ---------------------------------------------------------------------------------------------

BEFORE = [("certificates", "0008_signatory_columns")]


def migrate(targets) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)


def latest() -> list:
    return [key for key in MigrationExecutor(connection).loader.graph.leaf_nodes() if key[0] == "certificates"]


@pytest.mark.django_db(transaction=True)
def test_migration_fills_titles_links_versions_and_moves_no_activity(versions) -> None:
    activity, people, v1, v2 = versions
    issued = certificate_of(people[0])
    Certificate.objects.filter(pk=issued.pk).update(status=S.ISSUED, issued_at=timezone.now())
    CertificateSignatory.objects.update(previous_version=None)
    try:
        migrate(BEFORE)
    finally:
        migrate(latest())
    v2.refresh_from_db()
    assert v2.previous_version_id == v1.pk  # linked: same name and position, the older one inactive
    assert services.activity_signatories(activity)["left"].pk == v1.pk  # NOT moved (owner decision)
    issued.refresh_from_db()
    assert (issued.left_signatory_title, issued.right_signatory_title) == ("Director", "Vice President")
    assert services.outdated_ids(activity) == set()  # the titles match: nothing new is outdated
