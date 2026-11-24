"""Startup portal: profile, my registrations, registering while logged in (Step 5 rules), notifications.
Staff side: list/search, detail with history, sync and set-password link permissions."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from activities.models import ActivityStatus
from notifications.models import Notification
from registrations import services as registration_services
from registrations.models import FormAnswer, Registration
from startups import services
from startups.models import Startup

from .conftest import STARTUP_PASSWORD, answers_for, record
from registrations.tests.conftest import SIGN_JSON, SIGN_POST

pytestmark = pytest.mark.django_db

PORTAL = "/startups/portal/api"


@pytest.fixture
def portal(client, startup):
    """A client logged in as the startup."""
    response = client.post(f"{PORTAL}/login/", {"email": startup.email, "password": STARTUP_PASSWORD}, content_type="application/json")
    assert response.status_code == 200
    return client


def register(client, activity, **body):
    body.setdefault("answers", answers_for(activity))
    body.setdefault("designation", "Founder")  # Office / Agency is left out: the startup's name (ASSUMPTIONS 426)
    body.setdefault("privacy_consent", True)  # the privacy notice checkbox (ASSUMPTIONS 394)
    body.setdefault("signature", SIGN_JSON)  # required since ASSUMPTIONS 434
    return client.post(f"{PORTAL}/activities/{activity.pk}/register/", body, content_type="application/json")


# --- Profile ---------------------------------------------------------------------------------------


def test_my_profile_is_read_only_navigatu_data(portal, startup) -> None:
    Startup.objects.filter(pk=startup.pk).update(last_synced_at=timezone.now())
    data = portal.get(f"{PORTAL}/me/").json()["startup"]
    assert data["email"] == startup.email and data["navigatu_id"] == "NAV-1"
    assert data["maturity_stage"] == "Validation" and data["has_password"] is True
    assert data["last_synced_at"].endswith("+08:00")  # Manila time
    assert "password" not in data
    assert portal.post(f"{PORTAL}/me/", {}).status_code == 405  # nothing to edit


# --- Registering while logged in --------------------------------------------------------------------


def test_registration_sets_startup_and_uses_the_profile(portal, startup, open_activity, django_capture_on_commit_callbacks) -> None:
    activity = open_activity()
    with django_capture_on_commit_callbacks(execute=True):
        response = register(portal, activity)
    assert response.status_code == 201
    registration = Registration.objects.get()
    assert registration.startup == startup
    assert (registration.name, registration.email, registration.contact_number) == (
        startup.name, startup.email, startup.contact_number
    )
    assert registration.participant_category.name == "Incubatees"  # only one category: chosen automatically
    assert FormAnswer.objects.filter(registration=registration).count() == 1
    assert response.json()["registration"]["linked_to_startup"] is True
    assert mail.outbox and mail.outbox[-1].to == [startup.email]  # the Step 5 confirmation email


def test_startup_chooses_a_category_when_there_are_several(portal, open_activity) -> None:
    activity = open_activity(categories=("Faculty", "Incubatees"))
    missing = register(portal, activity)
    assert missing.status_code == 400 and "participant_category" in missing.json()["errors"]
    msmes = register(portal, activity, participant_category=999999)
    assert msmes.status_code == 400
    incubatees = activity.participant_categories.get(name="Incubatees")
    assert register(portal, activity, participant_category=incubatees.pk).status_code == 201


@pytest.mark.parametrize("status", [ActivityStatus.DRAFT, ActivityStatus.ONGOING, ActivityStatus.CONDUCTED])
def test_only_scheduled_activities(portal, open_activity, status) -> None:
    assert register(portal, open_activity(status=status)).status_code == 404


def test_step5_rules_still_apply(portal, startup, open_activity) -> None:
    full = open_activity(max_slots=1, title="Full")
    registration_services.register(full, name="Ana", email="ana@example.com", designation="Owner", office_agency="CSU", answers=answers_for(full))
    response = register(portal, full)
    assert response.status_code == 400 and "all slots are taken" in response.json()["error"]

    no_form = open_activity(with_form=False, title="No form")
    assert "form is not ready" in portal.post(f"{PORTAL}/activities/{no_form.pk}/register/", {"privacy_consent": True, "signature": SIGN_JSON}, content_type="application/json").json()["error"]

    open_one = open_activity(title="Open")
    assert "errors" in register(portal, open_one, answers={}).json()  # required answer missing
    assert register(portal, open_one).status_code == 201
    again = register(portal, open_one)
    assert again.status_code == 400 and "already registered" in again.json()["error"]


def test_deadline_passed_is_refused(startup, open_activity) -> None:
    activity = open_activity()
    later = activity.registration_deadline + timedelta(minutes=1)
    with pytest.raises(registration_services.RegistrationError, match="deadline"):
        services.register_as_startup(startup, activity, designation="Founder", answers=answers_for(activity), now=later)


def test_email_already_used_by_a_public_registration_is_refused(portal, startup, open_activity) -> None:
    activity = open_activity()
    registration_services.register(activity, name="Rep", email=startup.email.upper(), designation="Owner", office_agency="CSU", answers=answers_for(activity))
    response = register(portal, activity)
    assert response.status_code == 400 and "already registered" in response.json()["error"]


def test_public_registration_never_sets_startup(open_activity) -> None:
    activity = open_activity()
    registration = registration_services.register(activity, name="Ana", email="ana@example.com", designation="Owner", office_agency="CSU", answers=answers_for(activity))
    assert registration.startup is None


# --- My registrations ------------------------------------------------------------------------------------


def test_my_registrations_include_older_email_matches_without_changing_them(portal, startup, make_startup, open_activity) -> None:
    older_activity, linked_activity, other_activity = (
        open_activity(title="Older"), open_activity(title="Linked"), open_activity(title="Someone else")
    )
    older = registration_services.register(older_activity, name="Old rep", email="KALINAW@demo.example.com", designation="Owner", office_agency="CSU", answers=answers_for(older_activity)
    )
    register(portal, linked_activity)
    registration_services.register(other_activity, name="Bea", email="bea@example.com", designation="Owner", office_agency="CSU", answers=answers_for(other_activity))
    # A registration with my email but linked to ANOTHER startup is not mine.
    other_startup = make_startup(email="other@demo.example.com", name="Other")
    Registration.objects.filter(activity=other_activity).update(startup=other_startup)
    updated_at = Registration.objects.get(pk=older.pk).updated_at

    rows = portal.get(f"{PORTAL}/registrations/").json()["registrations"]
    titles = {row["activity_title"]: row for row in rows}
    assert set(titles) == {"Older", "Linked"}
    assert titles["Older"]["linked_to_startup"] is False and titles["Linked"]["linked_to_startup"] is True
    assert titles["Older"]["participant_category"] == "Incubatees"
    assert titles["Older"]["attendance_confirmed"] is False
    older.refresh_from_db()
    assert older.startup is None and older.updated_at == updated_at  # reading changed nothing


# --- My notifications -----------------------------------------------------------------------------------


def test_my_notifications_only_mine(portal, startup, make_startup, director) -> None:
    mine = Notification.objects.create(startup=startup, message="Welcome", type="startup_info")
    Notification.objects.create(startup=make_startup(email="other@demo.example.com"), message="Not mine", type="x")
    Notification.objects.create(member=director, message="Staff only", type="x")

    data = portal.get(f"{PORTAL}/notifications/").json()
    assert [n["notification_id"] for n in data["notifications"]] == [mine.pk] and data["unread_count"] == 1
    assert data["notifications"][0]["created_at"].endswith("+08:00")
    other = Notification.objects.exclude(pk=mine.pk).first()
    assert portal.post(f"{PORTAL}/notifications/{other.pk}/read/").status_code == 404
    assert portal.post(f"{PORTAL}/notifications/{mine.pk}/read/").json()["notification"]["is_read"] is True
    assert portal.get(f"{PORTAL}/notifications/unread-count/").json() == {"unread_count": 0}


def test_mark_all_read_only_mine(portal, startup, director) -> None:
    Notification.objects.create(startup=startup, message="a", type="x")
    Notification.objects.create(startup=startup, message="b", type="x")
    staff_note = Notification.objects.create(member=director, message="c", type="x")
    assert portal.post(f"{PORTAL}/notifications/read-all/").json() == {"marked_read": 2}
    staff_note.refresh_from_db()
    assert staff_note.is_read is False


# --- Staff side ---------------------------------------------------------------------------------------------


def test_staff_list_search_filters_and_pages(client, view_only, provider) -> None:
    provider([record(n, maturity_stage="Scaling" if n % 2 else "Ideation") for n in range(1, 24)] + [record(30, is_active=False, name="Gone Co")])
    services.sync_startups()
    client.force_login(view_only)  # any active staff member may look (item 12)
    first = client.get("/startups/api/").json()
    assert first["total"] == 24 and first["num_pages"] == 2 and len(first["startups"]) == 20
    assert client.get("/startups/api/", {"q": "gone"}).json()["total"] == 1
    assert client.get("/startups/api/", {"q": "startup7@"}).json()["total"] == 1  # email search
    assert client.get("/startups/api/", {"maturity_stage": "scaling"}).json()["total"] == 12
    assert client.get("/startups/api/", {"is_active": "false"}).json()["total"] == 1
    assert client.get("/startups/api/", {"is_active": "maybe"}).status_code == 400


def test_staff_detail_shows_history(client, view_only, startup, open_activity, director) -> None:
    activity = open_activity()
    registration = services.register_as_startup(startup, activity, designation="Founder", answers=answers_for(activity))
    Registration.objects.filter(pk=registration.pk).update(attendance_confirmed=True)
    client.force_login(view_only)
    data = client.get(f"/startups/api/{startup.pk}/").json()
    assert data["startup"]["email"] == startup.email
    assert data["registrations"][0]["attendance_confirmed"] is True
    assert data["registrations"][0]["days_attended"] == 0  # set directly in this test (no day rows)


def test_sync_and_links_need_can_manage_users(client, view_only, admin_member, startup, provider) -> None:
    provider([record(1, email=startup.email)])
    client.force_login(view_only)
    assert client.post("/startups/api/sync/").status_code == 403
    assert client.post(f"/startups/api/{startup.pk}/send-password-link/").status_code == 403

    client.force_login(admin_member)
    result = client.post("/startups/api/sync/").json()["result"]
    assert result["updated"] + result["unchanged"] == 1
    response = client.post(f"/startups/api/{startup.pk}/send-password-link/")
    assert response.status_code == 200 and "token" not in response.json() and "link" not in response.json()
    assert mail.outbox[-1].to == [startup.email]


def test_staff_api_requires_staff_login(client) -> None:
    assert client.get("/startups/api/").status_code == 401


def test_admin_is_view_only_with_link_action(client, admin_member, startup) -> None:
    client.force_login(admin_member)
    page = client.get(f"/admin/startups/startup/{startup.pk}/change/").content.decode()
    assert 'name="_save"' not in page  # no Save button: view only
    assert startup.password not in page  # the password hash is never shown
    assert client.get("/admin/startups/startup/add/").status_code == 403
    response = client.post(
        "/admin/startups/startup/", {"action": "send_password_links", "_selected_action": [startup.pk]}, follow=True
    )
    assert response.status_code == 200 and mail.outbox[-1].to == [startup.email]
