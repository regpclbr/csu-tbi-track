"""`manage.py seed_demo`: demo-only fake data."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.utils import timezone

from activities import attention
from activities.models import Activity
from documents.models import Document
from registrations.models import EvaluationSubmission, Registration
from accounts.management.commands.seed_demo import FORMULA_TEST_TITLE
from accounts.models import Role, TBIMember
from certificates.models import Certificate, CertificateTemplate
from reports import metrics
from startups.models import Startup


@pytest.fixture
def demo_settings(settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path  # placeholder files go to a temporary folder
    return settings


def run_demo() -> str:
    out = StringIO()
    call_command("seed_demo", stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_demo_refuses_to_run_when_debug_is_false(settings) -> None:
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG is False"):
        run_demo()
    assert Activity.objects.count() == 0


@pytest.mark.django_db
def test_demo_creates_prototype_activities(demo_settings) -> None:
    run_demo()
    assert set(Activity.objects.values_list("title", flat=True)) == {
        "Startup Pitch Competition", "Business Model Training", "Intellectual Property Seminar",
        "Mentoring Session", "Product Development Workshop", "Market Research Training",
        "Investor Networking", "Technology Forum",
        # Step 10: earlier Conducted activities for the dashboard and reports
        "Design Thinking Workshop", "Startup Finance Seminar", "Women in Tech Forum", "Digital Marketing Training",
        "Founder Mentoring Clinic", "Industry-Academe Networking Night", FORMULA_TEST_TITLE,
    }
    assert Startup.objects.count() == 10  # from the mock Navigatu sync (Step 7)
    assert Startup.objects.filter(is_active=True).count() == 9
    # ASSUMPTIONS 432: every demo upload is an approved copy (Approved by System), none waits for review.
    assert not Document.objects.filter(status__status_name="For Review").exists()
    assert Document.objects.filter(status__status_name="Approved", reviewed_by__isnull=True).exists()
    assert Document.objects.filter(status__status_name="Approved").exists()


@pytest.mark.django_db
def test_demo_shows_an_upcoming_activity_with_an_overdue_activity_design(demo_settings) -> None:
    """ASSUMPTIONS 416: the Activity Design is due 5 days before the start; the demo keeps one overdue example."""
    run_demo()
    workshop = Activity.objects.get(title="Product Development Workshop")
    assert workshop.status.name == "Scheduled" and workshop.start_date > timezone.localdate()
    texts = [item.text for item in attention.activity_attention(workshop)]
    overdue = next(text for text in texts if text.startswith("Overdue required documents:"))
    assert "Activity Design" in overdue


@pytest.mark.django_db
def test_demo_data_follows_business_rules(demo_settings) -> None:
    run_demo()
    # Never more registrations than slots.
    for activity in Activity.objects.all():
        assert activity.registrations.count() <= activity.max_slots
    # Only attendees evaluate.
    assert not EvaluationSubmission.objects.filter(registration__attendance_confirmed=False).exists()
    # Only fake emails.
    assert all(email.endswith("@demo.example.com") for email in Registration.objects.values_list("email", flat=True))


@pytest.mark.django_db
def test_demo_twice_creates_no_duplicates(demo_settings) -> None:
    run_demo()
    counts = (Activity.objects.count(), Registration.objects.count(), Document.objects.count())
    output = run_demo()
    assert (Activity.objects.count(), Registration.objects.count(), Document.objects.count()) == counts
    assert "0 activities created" in output


@pytest.mark.django_db
def test_demo_has_activities_with_two_categories(demo_settings) -> None:
    run_demo()
    pitch = Activity.objects.get(title="Startup Pitch Competition")
    assert sorted(c.name for c in pitch.participant_categories.all()) == ["Incubatees", "MSMEs"]
    assert all(a.participant_categories.exists() for a in Activity.objects.all())


@pytest.mark.django_db
def test_demo_gives_every_dashboard_card_and_chart_something_to_show(demo_settings, admin_member) -> None:
    run_demo()
    # seed_demo ANALYZEs what it loaded: PostgreSQL must not plan these tables as empty (see update_statistics).
    with connection.cursor() as cursor:
        cursor.execute("SELECT reltuples FROM pg_class WHERE relname = 'form_answer'")
        assert cursor.fetchone()[0] > 100
    today = timezone.localdate()  # demo dates are relative to today: use a rolling range, not the calendar year
    data = metrics.dashboard(metrics.ReportFilters(today - timedelta(days=300), today + timedelta(days=60)))
    cards = {card["key"]: card for card in data["cards"]}
    for key in ("activities_by_status", "upcoming_activities", "activities_conducted", "registrations", "evaluations",
                "documents_complete", "documents_overdue", "certificates_waiting_for_approval",
                "startups_engaged"):
        assert cards[key]["value"] > 0, key
    assert cards["documents_waiting_for_review"]["value"] == 0  # approved copies (ASSUMPTIONS 432)
    assert cards["certificates_waiting_for_approval"]["approved_not_issued"] > 0  # issued by retry_certificates / qcluster
    assert sum(1 for month in data["charts"]["per_month"] if month["activities"]) >= 3
    assert len(data["charts"]["by_type"]) >= 4
    assert all(row["activities"] for row in data["charts"]["by_category"])
    assert data["charts"]["average_rating_by_question"]


@pytest.mark.django_db
def test_demo_certificates_go_through_the_services_and_are_not_duplicated(demo_settings, admin_member) -> None:
    run_demo()
    statuses = set(Certificate.objects.values_list("status", flat=True))
    assert statuses == {"generated", "approved"}
    assert set(Certificate.objects.filter(status="approved").values_list("approved_by__role__role_name", flat=True)) == {"Director"}
    count = Certificate.objects.count()
    run_demo()
    assert Certificate.objects.count() == count


@pytest.mark.django_db
def test_demo_creates_one_account_per_role(demo_settings) -> None:
    """For showing the differences between roles (ASSUMPTIONS 339). Nobody can log in until a password is set with
    `python manage.py changepassword <email>`; no first-sign-in password change in the way of a demo."""
    run_demo()
    demo = TBIMember.objects.filter(email__endswith="@demo.example.com").select_related("role")
    by_role = {member.role.role_name: member for member in demo}
    assert set(by_role) == set(Role.objects.values_list("role_name", flat=True))
    assert by_role[Role.SYSTEM_ADMINISTRATOR].email == "system-administrator@demo.example.com"
    assert all(not member.has_usable_password() and not member.must_change_password for member in demo)


@pytest.mark.django_db
def test_demo_design_is_added_by_the_demo_system_administrator(demo_settings) -> None:
    """Without a real System Administrator, the demo one adds the invented design, so certificates are still made."""
    run_demo()
    template = CertificateTemplate.objects.get(name="[DEMO] Sample design")
    assert template.uploaded_by.email == "system-administrator@demo.example.com"
    assert Certificate.objects.exists()
