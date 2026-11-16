"""Fixtures for activity tests. They use the real seeded lookups (statuses, types, roles)."""

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from activities import services
from activities.models import Activity, ActivityType, ParticipantCategory
from registrations.models import FormTemplate, FormType, Registration


def at(day, hour: int = 17) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


# Tests with fixed calendar dates pass this as `now`, so they keep working after those dates pass.
TEST_NOW = timezone.make_aware(datetime(2026, 10, 1, 9, 0))


@pytest.fixture
def director(make_member):
    return make_member("Director")


@pytest.fixture
def valid_data(roles):
    """Factory: a complete, valid set of activity values, 30 days from today."""

    def make(**overrides) -> dict:
        start = timezone.localdate() + timedelta(days=30)
        end = start + timedelta(days=1)
        data = {
            "title": "Pitching Workshop",
            "description": "Learn to pitch.",
            "activity_type": ActivityType.objects.get(name="Workshop"),
            "participant_categories": [ParticipantCategory.objects.get(name="Incubatees")],
            "start_date": start,
            "end_date": end,
            "start_time": time(8, 0), "end_time": time(17, 0),
            "venue": "CSU Hall",
            "max_slots": 30,
            "registration_deadline": at(start - timedelta(days=2)),
            "evaluation_deadline": at(end + timedelta(days=7)),
            "budget_amount": Decimal("30000.00"),
        }
        data.update(overrides)
        return data

    return make


@pytest.fixture
def new_activity(director, valid_data) -> Activity:
    """A Draft activity created through the service."""
    return services.create_activity(director, valid_data())


@pytest.fixture
def move_to(director):
    """Walk an activity forward to a status through the allowed moves."""

    def move(activity: Activity, target: str) -> Activity:
        path = ["Scheduled", "Ongoing", "Conducted"]
        for status in path[: path.index(target) + 1]:
            services.change_status(director, activity, status)
        return activity

    return move


@pytest.fixture
def register(director):
    """Factory: register(activity, count) adds `count` registrations."""

    def make(activity: Activity, count: int = 1) -> list[Registration]:
        form, _ = FormTemplate.objects.get_or_create(
            activity=activity, form_type=FormType.REGISTRATION, defaults={"title": "Registration", "created_by": director}
        )
        start = activity.registrations.count()
        return [
            Registration.objects.create(
                activity=activity,
                template=form,
                participant_category=activity.participant_categories.first(),
                name=f"Person {n}",
                email=f"p{n}@example.com",
            )
            for n in range(start, start + count)
        ]

    return make
