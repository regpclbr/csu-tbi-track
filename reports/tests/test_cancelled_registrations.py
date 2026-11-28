"""Cancelled registrations (ASSUMPTIONS 440, owner decision 2026-11-25): "Will not attend" is NOT counted as registered in
the reports, the attendance rate, the participant mix or Needs attention, unless the person came anyway (then registered
AND attended); walk-ins count; "Cancelled: N" is shown separately. The slot rule is unchanged."""

from datetime import date

import pytest

from activities import attention
from activities.models import ActivityStatus
from activities.services import registered_count
from registrations.models import Registration
from reports import content, metrics

from .conftest import TEST_NOW, YEAR_2026, manila

pytestmark = pytest.mark.django_db


@pytest.fixture
def mixed(make_activity, register):
    """One Conducted activity (Faculty + Incubatees): 2 counted online (1 attended), 1 cancelled, 1 cancelled who came
    anyway, 1 walk-in who attended."""
    activity = make_activity("Mixed", categories=("Faculty", "Incubatees"))
    register(activity, "Ana", attended=True, category="Incubatees")
    register(activity, "Ben", category="Incubatees")
    carla = register(activity, "Carla", category="Faculty")
    dino = register(activity, "Dino", attended=True, category="Faculty")
    ella = register(activity, "Ella", attended=True, category="Incubatees")
    Registration.objects.filter(pk__in=[carla.pk, dino.pk]).update(cancelled_at=TEST_NOW)
    Registration.objects.filter(pk=ella.pk).update(registration_source=Registration.Source.WALK_IN)
    return activity


def test_period_summary(mixed) -> None:
    summary = metrics.period_summary(YEAR_2026, TEST_NOW)
    assert (summary["registered"], summary["cancelled"], summary["attended"]) == (4, 1, 3)
    assert summary["attendance_rate"] == 0.75  # 3 / 4: the cancelled one is out of the rate


def test_activity_rows_and_breakdowns_add_up(mixed) -> None:
    [row] = metrics.activity_rows(YEAR_2026)
    assert (row["registered"], row["cancelled"], row["attended"], row["attendance_rate"]) == (4, 1, 3, 0.75)
    assert sum(r["registered"] for r in metrics.by_type(YEAR_2026)) == 4
    assert sum(r["registered"] for r in metrics.by_category(YEAR_2026)) == 4
    assert sum(m["registered"] for m in metrics.per_month(YEAR_2026)) == 4


def test_participants_by_category_and_the_mix(mixed) -> None:
    by_category = metrics.participants_by_category([mixed.pk])[mixed.pk]
    rows = {row["category"]: row["registered"] for row in by_category["rows"]}
    assert by_category["registered"] == 4 and rows == {"Faculty": 1, "Incubatees": 3}  # Carla is not counted, Dino is
    mix = metrics.participant_mix([mixed.pk])[mixed.pk]
    assert mix["registered"] == 4 and mix["attended"] == 3


def test_the_mix_before_attendance_leaves_cancelled_ones_out(make_activity, register) -> None:
    activity = make_activity("Before", categories=("Faculty", "Incubatees"), status=ActivityStatus.SCHEDULED)
    register(activity, "Ana", category="Incubatees")
    Registration.objects.filter(pk=register(activity, "Carla", category="Faculty").pk).update(cancelled_at=TEST_NOW)
    mix = metrics.participant_mix([activity.pk])[activity.pk]
    assert mix["basis"] == "registered" and mix["registered"] == 1
    # 0 % Faculty: the cancelled Faculty member does not count (only Ana, so 100 % Incubatees: over the 70 % cap, 424)
    assert mix["check"]["problems"] == ["Incubatees 100% (at most 70%)"]


def test_one_activitys_evaluation_numbers(mixed) -> None:
    for data in (metrics.activity_evaluation_results(mixed), metrics.activity_evaluation_summary(mixed)):
        assert (data["registered"], data["cancelled"], data["attended"]) == (4, 1, 3)
    assert metrics.activity_registration_counts(mixed) == {
        "registered": 4, "cancelled": 1, "walk_ins": 1, "attended": 3, "attendance_rate": 0.75}


def test_needs_attention_ignores_cancelled_registrations(make_activity, register) -> None:
    activity = make_activity("Ended", status=ActivityStatus.ONGOING, start=date(2026, 10, 20))
    Registration.objects.filter(pk=register(activity, "Carla").pk).update(cancelled_at=TEST_NOW)
    texts = [item.text for item in attention.activity_attention(activity, now=manila(date(2026, 10, 23)))]
    assert attention.NO_REGISTRATIONS in texts  # only a cancellation: nobody registered


def test_the_report_summary_shows_the_cancelled_ones(mixed) -> None:
    section = content._summary(YEAR_2026, metrics.period_summary(YEAR_2026, TEST_NOW))
    rows = {row[0]: row[1] for row in section.tables[0].rows}
    assert rows["Registered participants"] == 4
    assert rows["Cancelled (“Will not attend”, not counted as registered)"] == 1


def test_the_slot_rule_is_unchanged(mixed) -> None:
    """activities.services.registered_count (slots) leaves every cancellation out, also one who came (item 434)."""
    assert registered_count(mixed) == 3
