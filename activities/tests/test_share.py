"""The Share card of the activity page and its QR codes (activities/share.py, ASSUMPTIONS 332-333, 336)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from registrations.tests.conftest import (  # noqa: F401  (fixtures used here)
    attended,
    defaults,
    opens_at,
    scheduled,
    signup,
    with_forms,
)

pytestmark = pytest.mark.django_db

SITE = "https://tbi.example.edu"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def site_url(settings) -> None:
    settings.SITE_URL = SITE


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def overview(client, activity) -> str:
    # A different Host than SITE_URL: the links must still use SITE_URL.
    return client.get(reverse("activities:detail", args=[activity.pk]), HTTP_HOST="localhost").content.decode()


def qr(activity, kind: str) -> str:
    return reverse("activities:qr_code", args=[activity.pk, kind])


def test_no_share_card_for_a_draft(director, login, new_activity) -> None:
    text = overview(login(director), new_activity)
    assert 'id="share-title"' not in text and qr(new_activity, "registration") not in text


def test_scheduled_shows_the_registration_link_from_site_url(view_only, login, scheduled) -> None:
    text = overview(login(view_only), scheduled)  # any staff member, also without can_create_activity
    url = f"{SITE}/register/{scheduled.pk}/"
    assert 'id="share-title"' in text and f'value="{url}"' in text and ">Open<" in text
    assert "http://localhost/register/" not in text  # never the request's Host
    assert qr(scheduled, "registration") in text and "Download QR (PNG)" in text
    assert qr(scheduled, "evaluation") not in text  # no evaluation section while Scheduled


def test_scheduled_but_full_is_marked_closed(director, login, scheduled, signup) -> None:
    scheduled.max_slots = 1
    scheduled.save(update_fields=["max_slots"])  # test setup only
    signup(scheduled, "ana@example.com")
    text = overview(login(director), scheduled)
    assert ">Closed<" in text and "all slots are taken" in text and f"{SITE}/register/{scheduled.pk}/" in text


def test_ongoing_shows_only_the_evaluation_section(director, login, attended, scheduled, clock) -> None:
    attended(1)  # now Ongoing
    # A new login after each clock jump: the staff session lasts 8 hours (ASSUMPTIONS 229).
    clock(opens_at(scheduled) - timedelta(minutes=1))
    text = overview(login(director), scheduled)
    assert 'id="share-title"' in text and "Registration link" not in text
    assert "The evaluation opens on" in text and qr(scheduled, "evaluation") not in text  # not open yet: no link
    clock(opens_at(scheduled))  # 00:00 Manila on the end date
    text = overview(login(director), scheduled)
    assert f'value="{SITE}/evaluation/{scheduled.pk}/"' in text and qr(scheduled, "evaluation") in text
    clock(scheduled.evaluation_deadline + timedelta(minutes=1))
    assert "The evaluation closed on" in overview(login(director), scheduled)


def test_qr_view_needs_a_staff_login(client, scheduled) -> None:
    response = client.get(qr(scheduled, "registration"))
    assert response.status_code == 302 and reverse("accounts:login") in response["Location"]


def test_qr_view_returns_a_png(view_only, login, scheduled) -> None:
    response = login(view_only).get(qr(scheduled, "registration"))
    assert response.status_code == 200 and response["Content-Type"] == "image/png"
    assert response.content.startswith(PNG_SIGNATURE) and "Content-Disposition" not in response
    download = login(view_only).get(qr(scheduled, "registration") + "?download=1")
    assert download["Content-Disposition"] == f'attachment; filename="activity-{scheduled.pk}-registration-qr.png"'
    assert download.content == response.content


def test_qr_encodes_the_site_url_link(scheduled, monkeypatch) -> None:
    from activities import share

    seen = []
    monkeypatch.setattr(share.segno, "make", lambda url, **kw: seen.append(url) or _NoImage())
    share.qr_png(share.qr_url(scheduled, "registration"))
    assert seen == [f"{SITE}/register/{scheduled.pk}/"]


class _NoImage:
    def save(self, buffer, **kwargs):
        buffer.write(b"")


def test_qr_view_404_when_the_card_does_not_show_the_link(director, login, valid_data, attended, scheduled, clock) -> None:
    from activities import services

    draft = services.create_activity(director, valid_data(title="Still a draft"))
    client = login(director)
    assert client.get(qr(draft, "registration")).status_code == 404
    assert client.get(qr(scheduled, "evaluation")).status_code == 404  # Scheduled: no evaluation link
    assert client.get(qr(scheduled, "unknown")).status_code == 404
    attended(1)  # Ongoing
    assert client.get(qr(scheduled, "registration")).status_code == 404  # registration section hidden
    clock(opens_at(scheduled) - timedelta(minutes=1))
    assert login(director).get(qr(scheduled, "evaluation")).status_code == 404  # not open yet
    clock(opens_at(scheduled))
    assert login(director).get(qr(scheduled, "evaluation")).content.startswith(PNG_SIGNATURE)
