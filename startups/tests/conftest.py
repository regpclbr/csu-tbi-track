"""Fixtures for startup tests: a fake provider, startups with passwords, staff, and an open activity."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory
from registrations.models import FormField, FormTemplate, FormType
from startups.models import Startup
from startups.providers import StartupProvider, StartupRecord

STARTUP_PASSWORD = "Startup-Pass-2026!"
FAKE_PROVIDER = "startups.tests.conftest.FakeProvider"


class FakeProvider(StartupProvider):
    """Test provider: returns whatever the test put in `records`."""

    records: list[StartupRecord] = []

    def list_startups(self) -> list[StartupRecord]:
        return list(self.records)

    def get_startup(self, navigatu_id: str) -> StartupRecord | None:
        return next((r for r in self.records if r.navigatu_id == navigatu_id), None)


def record(number: int, **changes) -> StartupRecord:
    """A Navigatu record: NAV-<number>, startup<number>@demo.example.com."""
    values = {
        "navigatu_id": f"NAV-{number}",
        "name": f"Startup {number}",
        "email": f"startup{number}@demo.example.com",
        "contact_number": f"0917000{number:04d}",
        "maturity_stage": "Validation",
        "last_assessment_at": timezone.make_aware(datetime(2026, 9, 1, 10, 0)),
        "is_active": True,
    }
    values.update(changes)
    return StartupRecord(**values)


@pytest.fixture
def provider(settings):
    """provider(records) makes the configured StartupProvider return these records."""
    settings.STARTUP_PROVIDER = FAKE_PROVIDER

    def use(records: list[StartupRecord]) -> None:
        FakeProvider.records = list(records)

    use([])
    yield use
    FakeProvider.records = []


@pytest.fixture
def make_startup():
    """Factory: make_startup(email=..., password=STARTUP_PASSWORD or None, **fields)."""

    def make(email: str = "kalinaw@demo.example.com", password: str | None = STARTUP_PASSWORD, **fields) -> Startup:
        fields.setdefault("name", "Kalinaw Agritech")
        fields.setdefault("contact_number", "09171234567")
        startup = Startup(email=email, **fields)
        if password:
            startup.set_password(password)
        startup.save()
        return startup

    return make


@pytest.fixture
def startup(make_startup) -> Startup:
    return make_startup(navigatu_id="NAV-1", maturity_stage="Validation")


@pytest.fixture
def director(make_member):
    return make_member("Director")


@pytest.fixture
def admin_staff(make_member):
    """Active staff without can_manage_users."""
    return make_member("Admin Staff")


@pytest.fixture
def open_activity(roles, director):
    """Factory: open_activity(categories=["Incubatees"], max_slots=30, status="Scheduled", days=10).

    A registration form with one required field. Dates are relative to now, so registration is open.
    """

    def make(categories=("Incubatees",), max_slots: int = 30, status: str = ActivityStatus.SCHEDULED,
             days: int = 10, title: str = "Pitching Workshop", with_form: bool = True) -> Activity:
        start = timezone.localdate() + timedelta(days=days)
        activity = Activity.objects.create(
            created_by=director,
            status=ActivityStatus.objects.get(name=status),
            activity_type=ActivityType.objects.get(name="Workshop"),
            title=title,
            start_date=start,
            end_date=start + timedelta(days=1),
            time_schedule="8:00 AM - 5:00 PM",
            venue="CSU Hall",
            max_slots=max_slots,
            registration_deadline=timezone.now() + timedelta(days=days - 2),
            evaluation_deadline=timezone.now() + timedelta(days=days + 10),
            budget_amount=Decimal("1000.00"),
        )
        activity.participant_categories.set(ParticipantCategory.objects.filter(name__in=categories))
        if with_form:
            form = FormTemplate.objects.create(
                activity=activity, form_type=FormType.REGISTRATION, title="Registration", created_by=director
            )
            FormField.objects.create(
                template=form, field_label="Affiliation / Organization", field_type="short_text", is_required=True
            )
        return activity

    return make


def answers_for(activity: Activity) -> dict:
    """Valid answers for the fixture's registration form."""
    field = FormField.objects.get(template__activity=activity, template__form_type=FormType.REGISTRATION)
    return {str(field.pk): "Kalinaw Agritech"}
