"""Phase F6: the startup portal pages (/startups/portal/...). The rules are tested with the services and the portal
API; these tests check that the pages use them, that staff and startups never cross (both directions, every page),
and that a startup only ever sees its own registrations, certificates and notifications."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.services import STARTUP_SCOPE, is_locked_out, lockout_message
from activities.models import ActivityStatus
from certificates.models import Certificate, CertificateSignatory, CertificateTemplate
from conftest import _stored, image_bytes
from notifications.models import Notification
from registrations import services as registration_services
from registrations.models import EvaluationSubmission, FormField, FormTemplate, FormType, Registration
from startups import portal_pages
from startups.decorators import STAFF_REFUSED

from .conftest import STARTUP_PASSWORD, answers_for

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
LOGIN = "/startups/portal/login/"

# Every portal page that needs a startup login (name, needs an activity id).
PORTAL_PAGES = [
    ("startups:portal_home", False),
    ("startups:portal_activities", False),
    ("startups:portal_register_page", True),
    ("startups:portal_registered", True),
    ("startups:portal_registrations_page", False),
    ("startups:portal_certificates_page", False),
    ("startups:portal_notifications_page", False),
    ("startups:portal_profile", False),
]
PORTAL_POSTS = ["startups:portal_read_all_page"]

# Every staff page (GET, no arguments): a startup session is never a staff login.
STAFF_PAGES = [
    "home", "search", "activities:list", "activities:create", "documents:list", "certificates:list", "startups:list",
    "reports:list", "assistant:chat", "notifications:list", "accounts:password_change", "accounts:console",
    "accounts:console_roles", "accounts:console_certificates", "accounts:console_system",
]


def portal_client(startup) -> Client:
    client = Client()
    response = client.post(LOGIN, {"email": startup.email, "password": STARTUP_PASSWORD})
    assert response.status_code == 302, response.content
    return client


def staff_client(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def portal(startup) -> Client:
    return portal_client(startup)


def page_url(name: str, with_id: bool, activity_id: int = 1) -> str:
    return reverse(name, args=[activity_id]) if with_id else reverse(name)


def make_registration(activity, startup=None, email="kalinaw@demo.example.com", **fields) -> Registration:
    form = FormTemplate.objects.get(activity=activity, form_type=FormType.REGISTRATION)
    return Registration.objects.create(
        activity=activity, template=form, participant_category=activity.participant_categories.first(),
        name="Kalinaw Agritech", email=email, startup=startup, **fields,
    )


@pytest.fixture
def past_activity(open_activity, director):
    """Factory: a Conducted activity that ended 4 days ago, with an evaluation form; its evaluation is open."""

    def make(title: str = "Market Validation Clinic"):
        activity = open_activity(status=ActivityStatus.CONDUCTED, days=-5, title=title)
        form = FormTemplate.objects.create(activity=activity, form_type=FormType.EVALUATION, title="Evaluation", created_by=director)
        FormField.objects.create(template=form, field_label="Overall rating", field_type="rating", is_required=True)
        return activity

    return make


# --- Login ---------------------------------------------------------------------------------------


def test_login_page_has_its_own_title_the_contact_and_the_link_back(client, settings) -> None:
    settings.TBI_CONTACT_EMAIL = "tbi.help@example.com"
    html = client.get(LOGIN).content.decode()
    assert "Startup portal" in html and "Forgot your password? Ask the TBI to send you a new link" in html
    assert 'href="mailto:tbi.help@example.com"' in html
    assert f'href="{reverse("accounts:login")}"' in html  # back to the staff login


def test_the_staff_login_links_to_the_startup_portal(client) -> None:
    html = client.get(reverse("accounts:login")).content.decode()
    assert "Are you a startup? Sign in to the startup portal" in html and f'href="{LOGIN}"' in html


def test_login_goes_to_the_overview(client, startup) -> None:
    response = client.post(LOGIN, {"email": startup.email.upper(), "password": STARTUP_PASSWORD})
    assert response.status_code == 302 and response["Location"] == reverse("startups:portal_home")
    assert client.get(LOGIN)["Location"] == reverse("startups:portal_home")  # already logged in


def test_login_follows_only_a_portal_next(client, startup) -> None:
    data = {"email": startup.email, "password": STARTUP_PASSWORD}
    assert client.post(f"{LOGIN}?next=/startups/portal/profile/", data)["Location"] == "/startups/portal/profile/"
    for unsafe in ("/activities/", "https://evil.example.com/startups/portal/", "//evil.example.com/startups/portal/"):
        other = Client()
        assert other.post(f"{LOGIN}?next={unsafe}", data)["Location"] == reverse("startups:portal_home")


def test_wrong_password_shows_the_service_message_and_locks_out(client, startup, make_member) -> None:
    for _ in range(5):
        response = client.post(LOGIN, {"email": startup.email, "password": "wrong"})
        assert response.status_code == 400 and "Invalid email or password." in response.content.decode()
    html = client.post(LOGIN, {"email": startup.email, "password": STARTUP_PASSWORD}).content.decode()
    assert lockout_message() in html
    assert is_locked_out(startup.email, "127.0.0.1", STARTUP_SCOPE)
    assert not is_locked_out(startup.email, "127.0.0.1")  # the staff counters are separate


def test_a_staff_member_cannot_use_the_portal_login(admin_member) -> None:
    client = staff_client(admin_member)
    response = client.get(LOGIN)
    assert response.status_code == 403 and STAFF_REFUSED in response.content.decode()


def test_logout_ends_the_portal_session(portal) -> None:
    assert portal.post(reverse("startups:portal_logout_page"))["Location"] == LOGIN
    assert portal.get(reverse("startups:portal_home")).status_code == 302
    assert portal.get(reverse("startups:portal_logout_page")).status_code == 405  # POST only


# --- Staff and startups never cross (every page, both directions) ----------------------------------


@pytest.mark.parametrize(("name", "with_id"), PORTAL_PAGES)
def test_portal_pages_need_a_startup_login(client, name, with_id) -> None:
    url = page_url(name, with_id)
    response = client.get(url)
    assert response.status_code == 302 and response["Location"] == f"{LOGIN}?next={url}"
    htmx = client.get(url, **HTMX)
    assert htmx.status_code == 401 and htmx["HX-Redirect"].startswith(LOGIN)


@pytest.mark.parametrize(("name", "with_id"), PORTAL_PAGES)
def test_staff_can_never_open_portal_pages(admin_member, name, with_id) -> None:
    response = staff_client(admin_member).get(page_url(name, with_id))
    assert response.status_code == 403 and STAFF_REFUSED in response.content.decode()


@pytest.mark.parametrize("name", PORTAL_POSTS)
def test_staff_can_never_post_to_the_portal(admin_member, name) -> None:
    assert staff_client(admin_member).post(reverse(name)).status_code == 403


@pytest.mark.parametrize("name", STAFF_PAGES)
def test_a_startup_can_never_open_staff_pages(portal, name) -> None:
    response = portal.get(reverse(name))
    assert response.status_code == 302 and response["Location"].startswith(reverse("accounts:login"))


def test_a_startup_can_never_open_the_django_admin(portal) -> None:
    assert portal.get("/admin/").status_code == 302


@pytest.mark.parametrize(("name", "with_id"), [p for p in PORTAL_PAGES if not p[1]])
def test_every_portal_page_opens_for_a_startup(portal, name, with_id) -> None:
    html = portal.get(page_url(name, with_id)).content.decode()
    assert 'aria-label="Portal navigation"' in html and "Kalinaw Agritech" in html and "kalinaw@demo.example.com" in html
    assert 'aria-current="page"' in html  # its own sidebar item is marked
    assert reverse("startups:portal_logout_page") in html


# --- Overview and open activities -------------------------------------------------------------------


def test_overview_shows_my_next_activity_counts_and_new_open_activities(portal, startup, open_activity, past_activity) -> None:
    mine = open_activity(title="Pitch Clinic", days=6)
    open_activity(title="Investor Day", days=12)
    make_registration(mine, startup=startup)
    make_registration(past_activity(), startup=startup, attendance_confirmed=True)
    html = portal.get(reverse("startups:portal_home")).content.decode()
    assert "Welcome, Kalinaw Agritech!" in html
    next_card = html.split('id="next-title"')[1].split("</section>")[0]
    assert "Pitch Clinic" in next_card
    open_section = html.split('id="open-title"')[1]
    assert "Investor Day" in open_section and "Pitch Clinic" not in open_section  # only ones I haven't registered for
    assert portal_pages.overview(startup)["counts"] == {"registered": 2, "attended": 1, "certificates": 0}


def test_open_activities_mark_mine_as_already_registered(portal, startup, open_activity) -> None:
    mine = open_activity(title="Pitch Clinic")
    other = open_activity(title="Investor Day")
    make_registration(mine, startup=None, email=startup.email.upper())  # an older public registration with my email
    html = portal.get(reverse("startups:portal_activities")).content.decode()
    assert "Already registered" in html
    assert reverse("startups:portal_register_page", args=[other.pk]) in html
    assert reverse("startups:portal_register_page", args=[mine.pk]) not in html


def test_no_open_activities_shows_an_empty_state(portal) -> None:
    assert "No activities are open right now" in portal.get(reverse("startups:portal_activities")).content.decode()


# --- Registering ------------------------------------------------------------------------------------


def test_register_form_shows_my_profile_read_only(portal, open_activity) -> None:
    activity = open_activity()
    html = portal.get(reverse("startups:portal_register_page", args=[activity.pk])).content.decode()
    assert "Registering as" in html and "09171234567" in html and "Changes are made in Navigatu" in html
    assert 'name="name"' not in html and 'name="email"' not in html  # never typed: they come from the profile
    assert "Affiliation / Organization" in html  # the activity's field (components/answer_field.html)


def test_registering_uses_the_portal_service_and_shows_the_success_page(portal, startup, open_activity, django_capture_on_commit_callbacks) -> None:
    activity = open_activity()
    field_id = next(iter(answers_for(activity)))
    with django_capture_on_commit_callbacks(execute=True):
        response = portal.post(reverse("startups:portal_register_page", args=[activity.pk]), {f"field_{field_id}": "Kalinaw"})
    assert response["Location"] == reverse("startups:portal_registered", args=[activity.pk])
    registration = Registration.objects.get()
    assert registration.startup == startup and registration.email == startup.email
    html = portal.get(response["Location"]).content.decode()
    assert "You're registered!" in html and reverse("register_calendar", args=[activity.pk]) in html
    # Again: already registered, never a second row.
    again = portal.get(reverse("startups:portal_register_page", args=[activity.pk])).content.decode()
    assert "You&#x27;re already registered" in again and Registration.objects.count() == 1


def test_register_errors_appear_under_their_fields(portal, open_activity) -> None:
    activity = open_activity(categories=("Faculty", "Incubatees"))
    response = portal.post(reverse("startups:portal_register_page", args=[activity.pk]), {})
    html = response.content.decode()
    assert response.status_code == 400 and 'id="id_participant_category-error"' in html
    assert "Please check the 2 fields marked below." in html and not Registration.objects.exists()


def test_the_success_page_only_right_after_registering(portal, open_activity) -> None:
    activity = open_activity()
    response = portal.get(reverse("startups:portal_registered", args=[activity.pk]))
    assert response["Location"] == reverse("startups:portal_registrations_page")


def test_a_closed_or_draft_activity_cannot_be_registered_for(portal, open_activity) -> None:
    full = open_activity(max_slots=1, title="Full")
    make_registration(full, email="someone@example.com")
    html = portal.get(reverse("startups:portal_register_page", args=[full.pk])).content.decode()
    assert "Registration is closed" in html and registration_services.registration_closed_reason(full) in html
    draft = open_activity(status=ActivityStatus.DRAFT, title="Draft")
    assert portal.get(reverse("startups:portal_register_page", args=[draft.pk])).status_code == 404


# --- My registrations ---------------------------------------------------------------------------------


def test_my_registrations_show_attendance_and_the_evaluation_only_when_open(portal, startup, open_activity, past_activity, make_startup) -> None:
    make_registration(open_activity(title="Pitch Clinic"), startup=startup)
    attended = make_registration(past_activity("Attended Clinic"), startup=startup, attendance_confirmed=True)
    missed = make_registration(past_activity("Missed Clinic"), startup=startup)
    done = make_registration(past_activity("Evaluated Clinic"), startup=startup, attendance_confirmed=True)
    EvaluationSubmission.objects.create(registration=done, template=FormTemplate.objects.get(activity=done.activity, form_type=FormType.EVALUATION),
                                        certificate_name="Kalinaw", certificate_email=startup.email)
    other = make_startup(email="other@demo.example.com", name="Someone Else")
    make_registration(past_activity("Not Mine"), startup=other, email=other.email, attendance_confirmed=True)

    html = portal.get(reverse("startups:portal_registrations_page")).content.decode()
    upcoming_part, past_part = html.split('id="past-title"')
    assert "Pitch Clinic" in upcoming_part and "Not recorded yet" in upcoming_part and portal_pages.UPCOMING_EVALUATION in upcoming_part
    assert reverse("evaluate", args=[registration_services.evaluation_token(attended)]) in past_part
    assert reverse("evaluate", args=[registration_services.evaluation_token(missed)]) not in past_part
    assert registration_services.NOT_ATTENDED in past_part and "Not marked as attended" in past_part
    assert "Evaluation submitted" in past_part
    assert "Not Mine" not in html


def test_no_registrations_shows_empty_states(portal) -> None:
    html = portal.get(reverse("startups:portal_registrations_page")).content.decode()
    assert "No upcoming registrations" in html and "No past activities yet" in html


# --- My certificates ------------------------------------------------------------------------------------


def _certificate(registration, number: str, director, status=Certificate.Status.ISSUED) -> Certificate:
    template = CertificateTemplate.objects.first() or CertificateTemplate.objects.create(
        name="Design", file_path=_stored("certificate_templates/d.png", image_bytes()), uploaded_by=director, is_active=True)
    vp = CertificateSignatory.objects.filter(position="Vice President", default_slot="right").first() or CertificateSignatory.objects.create(
        name="VP", position="Vice President", default_slot="right", signature_image_path=_stored("signatures/vp.png", image_bytes()))
    head = CertificateSignatory.objects.filter(position="Director", default_slot="left").first() or CertificateSignatory.objects.create(
        name="Director", position="Director", default_slot="left", signature_image_path=_stored("signatures/d.png", image_bytes()))
    return Certificate.objects.create(
        certificate_template=template, certificate_number=number, recipient_type=Certificate.RecipientType.REGISTRATION,
        registration=registration, activity=registration.activity, right_signatory=vp, left_signatory=head,
        delivery_method=Certificate.DeliveryMethod.EMAIL, status=status, issued_at=timezone.now(),
        file_path=_stored(f"certificates/{number}.pdf", b"%PDF-1.4 test"),
    )


def test_my_certificates_list_only_my_issued_ones(portal, startup, past_activity, make_startup, director) -> None:
    mine = _certificate(make_registration(past_activity("Mine"), startup=startup, attendance_confirmed=True), "TBI-2026-00001", director)
    _certificate(make_registration(past_activity("Approved only"), startup=startup, attendance_confirmed=True), "TBI-2026-00002",
                 director, status=Certificate.Status.APPROVED)
    other = make_startup(email="other@demo.example.com", name="Someone Else")
    theirs = _certificate(make_registration(past_activity("Theirs"), startup=other, email=other.email), "TBI-2026-00003", director)
    html = portal.get(reverse("startups:portal_certificates_page")).content.decode()
    assert "TBI-2026-00001" in html and reverse("startups:portal_certificate_download", args=[mine.pk]) in html
    assert "TBI-2026-00002" not in html and "TBI-2026-00003" not in html
    assert portal.get(reverse("startups:portal_certificate_download", args=[mine.pk])).status_code == 200
    assert portal.get(reverse("startups:portal_certificate_download", args=[theirs.pk])).status_code == 404


def test_no_certificates_shows_an_empty_state(portal) -> None:
    assert "No certificates yet" in portal.get(reverse("startups:portal_certificates_page")).content.decode()


# --- Notifications and profile -------------------------------------------------------------------------------


def test_notifications_are_mine_and_can_be_marked_read(portal, startup, make_startup) -> None:
    other = make_startup(email="other@demo.example.com", name="Someone Else")
    mine = Notification.objects.create(startup=startup, type="announcement", message="Welcome to the portal.")
    Notification.objects.create(startup=startup, type="announcement", message="Second message.")
    theirs = Notification.objects.create(startup=other, type="announcement", message="Not for you.")
    html = portal.get(reverse("startups:portal_notifications_page")).content.decode()
    assert "Welcome to the portal." in html and "Not for you." not in html
    assert portal.post(reverse("startups:portal_read_page", args=[theirs.pk])).status_code == 404
    response = portal.post(reverse("startups:portal_read_page", args=[mine.pk]), {"next": "/startups/portal/notifications/?show=unread"})
    assert response["Location"] == "/startups/portal/notifications/?show=unread"
    unread = portal.get(f"{reverse('startups:portal_notifications_page')}?show=unread").content.decode()
    assert "Second message." in unread and "Welcome to the portal." not in unread
    assert portal.post(reverse("startups:portal_read_all_page"), {"next": "/accounts/console/"})["Location"] == reverse("startups:portal_notifications_page")
    assert not Notification.objects.filter(startup=startup, is_read=False).exists()
    theirs.refresh_from_db()
    assert theirs.is_read is False


def test_profile_is_read_only_navigatu_data(portal, startup) -> None:
    html = portal.get(reverse("startups:portal_profile")).content.decode()
    assert "NAV-1" in html and "Validation" in html and "Changes are made in Navigatu" in html
    assert "<form" not in html.split('id="profile-title"')[1].split("</section>")[0]  # nothing to edit
    assert startup.password not in html
    assert portal.post(reverse("startups:portal_profile")).status_code == 405


def test_a_deactivated_startup_is_logged_out_on_the_next_page(portal, startup) -> None:
    startup.is_active = False
    startup.save()
    assert portal.get(reverse("startups:portal_home"))["Location"].startswith(LOGIN)
