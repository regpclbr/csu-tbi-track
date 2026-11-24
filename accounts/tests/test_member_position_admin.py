"""The member's position = the official job title printed under "Prepared by" (ASSUMPTIONS 429): the existing POSITION
list (item 390), set by the member on My profile or by the System Administrator on the Members screen
(accounts.services.set_member_position). One position per member; never a permission. No new column."""

import logging

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import Position, TBIMember

pytestmark = pytest.mark.django_db

TITLE = "Admin Aide VI (Clerk III)"


@pytest.fixture
def clerk_title() -> Position:
    return Position.objects.create(name=TITLE)


def test_the_list_holds_an_official_title_and_a_member_has_one(admin_member, make_member, clerk_title) -> None:
    staff = make_member("Admin Staff")
    assert TBIMember._meta.get_field("position").many_to_one  # one value per member
    assert len("University Research Associate I") <= Position._meta.get_field("name").max_length
    services.set_member_position(admin_member, staff, clerk_title.pk)
    staff.refresh_from_db()
    assert staff.position == clerk_title


def test_inactive_positions_only_stay_for_the_member_who_has_them(admin_member, make_member, clerk_title) -> None:
    staff, other = make_member("Admin Staff"), make_member("Incubation Project Head")
    services.set_member_position(admin_member, staff, clerk_title.pk)
    services.set_position_active(admin_member, clerk_title, False)
    services.set_member_position(admin_member, staff, clerk_title.pk)  # kept: their own
    with pytest.raises(services.MemberManagementError, match="Choose a position"):
        services.set_member_position(admin_member, other, clerk_title.pk)
    with pytest.raises(services.MemberManagementError):
        services.set_member_position(admin_member, other, 999999)
    services.set_member_position(admin_member, staff, None)
    staff.refresh_from_db()
    assert staff.position is None


def test_only_the_system_administrator(make_member, clerk_title) -> None:
    director, staff = make_member("Director"), make_member("Admin Staff")
    with pytest.raises(PermissionDenied):
        services.set_member_position(director, staff, clerk_title.pk)


def test_logged_with_ids_only(admin_member, make_member, clerk_title, caplog) -> None:
    staff = make_member("Admin Staff")
    with caplog.at_level(logging.INFO, logger="accounts.audit"):
        services.set_member_position(admin_member, staff, clerk_title.pk)
    line = next(r.getMessage() for r in caplog.records if "position changed" in r.getMessage())
    assert f"-> {clerk_title.pk}" in line and TITLE not in line


def test_members_screen_sets_it(admin_member, make_member, clerk_title) -> None:
    staff = make_member("Admin Staff")
    client = Client()
    client.force_login(admin_member)
    page = client.get(reverse("accounts:console")).content.decode()
    assert f'id="position-{staff.pk}"' in page and TITLE in page
    response = client.post(reverse("accounts:console_member_position", args=[staff.pk]), {"position": clerk_title.pk},
                           follow=True)
    staff.refresh_from_db()
    assert staff.position == clerk_title and f"position is now {TITLE}" in response.content.decode()
    client.post(reverse("accounts:console_member_position", args=[staff.pk]), {"position": ""})
    staff.refresh_from_db()
    assert staff.position is None
    refused = client.post(reverse("accounts:console_member_position", args=[staff.pk]), {"position": "abc"}, follow=True)
    assert services.POSITION_NOT_OFFERED in refused.content.decode()


def test_members_screen_needs_manage_users(make_member, clerk_title) -> None:
    director, staff = make_member("Director"), make_member("Admin Staff")
    client = Client()
    client.force_login(director)
    assert client.post(reverse("accounts:console_member_position", args=[staff.pk]),
                       {"position": clerk_title.pk}).status_code == 403
