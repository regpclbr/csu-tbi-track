"""Phase F5: the staff Startups pages (/startups/, /startups/<id>/), "Sync now" and "Send set-password link"
(can_manage_users only: hidden and refused without it), and services.maturity_stages()."""

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from registrations.models import FormTemplate, FormType, Registration
from startups import services
from startups.models import Startup

from .conftest import record

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


# --- List -------------------------------------------------------------------------------------------------------------


def test_list_shows_startups_with_filters(view_only, login, make_startup) -> None:
    make_startup(name="Kalinaw Agritech", maturity_stage="Validation")
    make_startup(email="old@demo.example.com", name="Old Co", maturity_stage="Scaling", is_active=False)
    html = login(view_only).get(reverse("startups:list")).content.decode()
    assert "is coming soon" not in html and "Kalinaw Agritech" in html and "Old Co" in html
    assert '<option value="Scaling">Scaling</option>' in html and '<option value="Validation">Validation</option>' in html
    assert "2 startups" in html
    assert "Sync now" not in html  # no can_manage_users


@pytest.mark.parametrize(("query", "shown", "hidden"), [
    ("q=kalinaw", "Kalinaw Agritech", "Old Co"),
    ("q=old%40demo", "Old Co", "Kalinaw Agritech"),
    ("maturity_stage=scaling", "Old Co", "Kalinaw Agritech"),
    ("is_active=true", "Kalinaw Agritech", "Old Co"),
    ("is_active=false", "Old Co", "Kalinaw Agritech"),
])
def test_filters_swap_only_the_results(view_only, login, make_startup, query, shown, hidden) -> None:
    make_startup(name="Kalinaw Agritech", maturity_stage="Validation")
    make_startup(email="old@demo.example.com", name="Old Co", maturity_stage="Scaling", is_active=False)
    html = login(view_only).get(f"{reverse('startups:list')}?{query}", **HTMX).content.decode()
    assert html.lstrip().startswith('<div id="startup-results"')
    assert shown in html and hidden not in html and "matching the filters" in html


def test_an_unreadable_filter_is_ignored_with_a_note(view_only, login, startup) -> None:
    html = login(view_only).get(reverse("startups:list") + "?is_active=maybe").content.decode()
    assert "ignored: active" in html and startup.name in html


def test_empty_states(view_only, login) -> None:
    client = login(view_only)
    assert "No startups yet" in client.get(reverse("startups:list")).content.decode()
    html = client.get(reverse("startups:list") + "?q=zzz").content.decode()
    assert "No startups match the filters" in html and "Clear filters" in html


# --- Detail ----------------------------------------------------------------------------------------------------------


def test_detail_shows_the_profile_and_the_history(view_only, login, startup, open_activity) -> None:
    activity = open_activity()
    form = FormTemplate.objects.get(activity=activity, form_type=FormType.REGISTRATION)
    category = activity.participant_categories.get()
    Registration.objects.create(activity=activity, template=form, participant_category=category, startup=startup,
                                name="Kalinaw", email=startup.email, attendance_confirmed=True,
                                confirmation_method=Registration.ConfirmationMethod.EMAIL)
    Registration.objects.create(activity=open_activity(title="Old Forum"), template=FormTemplate.objects.filter(
        activity__title="Old Forum").get(), participant_category=category, name="K", email=startup.email.upper())
    html = login(view_only).get(reverse("startups:detail", args=[startup.pk])).content.decode()
    for text in (startup.email, "NAV-1", "Validation", "Pitching Workshop", "Old Forum", "Attended", "Not marked",
                 "Public form (email match)", "2 registrations, 1 attended", "cannot be edited here"):
        assert text in html
    assert "Send set-password link" not in html


def test_unknown_startup_is_404(view_only, login) -> None:
    assert login(view_only).get(reverse("startups:detail", args=[999999])).status_code == 404


def test_pages_need_a_staff_login(client, startup) -> None:
    assert client.get(reverse("startups:list")).status_code == 302
    assert client.get(reverse("startups:detail", args=[startup.pk])).status_code == 302


# --- Sync now and set-password links (can_manage_users) ------------------------------------------------------------


def test_admin_sees_the_buttons(admin_member, login, startup) -> None:
    client = login(admin_member)
    assert "Sync now" in client.get(reverse("startups:list")).content.decode()
    assert "Send set-password link" in client.get(reverse("startups:detail", args=[startup.pk])).content.decode()


def test_no_link_button_for_an_inactive_startup(admin_member, login, make_startup) -> None:
    inactive = make_startup(is_active=False)
    assert "Send set-password link" not in login(admin_member).get(reverse("startups:detail", args=[inactive.pk])).content.decode()


def test_sync_and_links_are_refused_without_can_manage_users(director, login, startup, provider) -> None:
    provider([record(1)])
    client = login(director)
    assert client.post(reverse("startups:sync")).status_code == 403
    assert client.post(reverse("startups:send_password_link", args=[startup.pk])).status_code == 403
    assert Startup.objects.count() == 1 and not mail.outbox


def test_sync_now_runs_the_sync_and_says_what_happened(admin_member, login, provider) -> None:
    provider([record(1), record(2)])
    response = login(admin_member).post(reverse("startups:sync"), {"next": "/startups/?q=x"}, follow=True)
    assert response.redirect_chain[-1][0] == "/startups/?q=x"
    assert messages_of(response) == ["Sync finished: 2 new, 0 updated, 0 unchanged, 0 deactivated, 0 skipped."]
    assert Startup.objects.count() == 2


def test_sync_now_reports_the_safety_guard(admin_member, login, provider, make_startup) -> None:
    for number in range(1, 4):
        make_startup(email=f"startup{number}@demo.example.com", navigatu_id=f"NAV-{number}")
    provider([record(1)])  # two of three would be deactivated: the guard holds them back
    response = login(admin_member).post(reverse("startups:sync"), follow=True)
    [message] = response.context["messages"]
    assert message.level_tag == "warning" and "Nobody was deactivated" in str(message)
    assert Startup.objects.filter(is_active=True).count() == 3


def test_sync_now_shows_navigatu_not_available(admin_member, login, settings) -> None:
    settings.STARTUP_PROVIDER = "startups.providers.NavigatuStartupProvider"
    response = login(admin_member).post(reverse("startups:sync"), follow=True)
    [message] = response.context["messages"]
    assert message.level_tag == "error" and str(message) == "The Navigatu connection is not available yet."


def test_send_link_emails_it_and_never_shows_it(admin_member, login, startup) -> None:
    response = login(admin_member).post(reverse("startups:send_password_link", args=[startup.pk]), follow=True)
    assert messages_of(response) == [f"A set-password link was emailed to {startup.email}."]
    [email] = mail.outbox
    assert "/startups/set-password/" in email.body and "/startups/set-password/" not in response.content.decode()


def test_send_link_to_an_inactive_startup_is_refused_with_the_reason(admin_member, login, make_startup) -> None:
    inactive = make_startup(is_active=False)
    response = login(admin_member).post(reverse("startups:send_password_link", args=[inactive.pk]), follow=True)
    assert messages_of(response) == ["This startup is not active, so it cannot log in."] and not mail.outbox


# --- maturity_stages() ----------------------------------------------------------------------------------------------


def test_maturity_stages_are_distinct_ignoring_case_and_spaces(make_startup) -> None:
    make_startup(email="a@demo.example.com", maturity_stage="Validation")
    make_startup(email="b@demo.example.com", maturity_stage=" validation ")
    make_startup(email="c@demo.example.com", maturity_stage="Early Traction")
    make_startup(email="d@demo.example.com", maturity_stage="  ")
    make_startup(email="e@demo.example.com", maturity_stage=None)
    assert services.maturity_stages() == ["Early Traction", "Validation"]
