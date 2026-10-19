"""`manage.py seed_demo`: demo-only fake data."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from activities.models import Activity
from documents.models import Document
from registrations.models import EvaluationSubmission, Registration
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
    }
    assert Startup.objects.count() == 6
    assert Document.objects.filter(status__status_name="For Review").exists()
    assert Document.objects.filter(status__status_name="Approved").exists()


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
