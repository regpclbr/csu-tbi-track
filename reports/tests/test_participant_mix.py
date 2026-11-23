"""Participant mix and NAVIGATU's guideline (ASSUMPTIONS 424): Faculty at most 30 %, Incubatees (with MSMEs, until
NAVIGATU confirms) at least 70 %; checked only for activities open to Faculty and to Incubatees or MSMEs; attendees
once anybody attended, else registrations; a warning in Needs attention, never a block."""

from datetime import date

import pytest
from django.urls import reverse

from activities import attention
from activities.models import ActivityStatus, ParticipantCategory
from reports import metrics
from reports.dashboard_page import mix_chart

pytestmark = pytest.mark.django_db

MIXED = ("Faculty", "Incubatees", "MSMEs")


def people(register, activity, category: str, count: int, attended: bool = False) -> None:
    for n in range(count):
        register(activity, f"{category} {n} {attended}", category=category, attended=attended)


def mix_of(activity) -> dict:
    return metrics.participant_mix([activity.pk])[activity.pk]


@pytest.mark.parametrize(("faculty", "incubatees", "total", "within"), [
    (3, 7, 10, True),  # exactly 30 % and 70 %: inside
    (4, 6, 10, False),
    (0, 10, 10, True),
    (2, 7, 10, True),  # 1 in another category: 20 % Faculty, 70 % Incubatees + MSMEs
    (2, 6, 10, False),  # Faculty fine, but only 60 % Incubatees + MSMEs
])
def test_mix_check_boundaries(faculty, incubatees, total, within) -> None:
    assert metrics.mix_check(faculty, incubatees, total)["within"] is within


def test_mix_check_explains_each_problem_and_skips_an_empty_mix() -> None:
    assert metrics.mix_check(0, 0, 0) is None
    assert metrics.mix_check(9, 11, 20)["problems"] == ["Faculty 45% (at most 30%)", "Incubatees and MSMEs 55% (at least 70%)"]


def test_msmes_count_with_the_incubatees(make_activity, register) -> None:
    activity = make_activity(status=ActivityStatus.SCHEDULED, categories=MIXED)
    people(register, activity, "Faculty", 3)
    people(register, activity, "Incubatees", 2)
    people(register, activity, "MSMEs", 5)
    mix = mix_of(activity)
    assert mix["basis"] == "registered" and mix["check"] == {"within": True, "problems": []}
    rows = {row["group"]: row for row in mix["rows"]}
    assert (rows["Faculty"]["registered"], rows["Faculty"]["registered_share"]) == (3, 0.3)
    assert rows["MSMEs"]["registered_share"] == 0.5 and "Other categories" not in rows


def test_attendees_are_checked_once_anybody_attended(make_activity, register) -> None:
    activity = make_activity(status=ActivityStatus.ONGOING, categories=MIXED)
    people(register, activity, "Incubatees", 8)  # registrations alone would be fine
    people(register, activity, "Faculty", 2, attended=True)
    people(register, activity, "Incubatees", 1, attended=True)
    mix = mix_of(activity)
    assert mix["basis"] == "attended" and mix["registered"] == 11 and mix["attended"] == 3
    assert mix["check"]["problems"][0] == "Faculty 67% (at most 30%)"


def test_only_activities_open_to_faculty_and_incubatees_or_msmes_are_checked(make_activity, register) -> None:
    faculty_only = make_activity(status=ActivityStatus.SCHEDULED, categories=("Faculty",))
    people(register, faculty_only, "Faculty", 5)
    assert mix_of(faculty_only)["check"] is None and not mix_of(faculty_only)["checked"]
    with_msmes = make_activity(status=ActivityStatus.SCHEDULED, categories=("Faculty", "MSMEs"))
    people(register, with_msmes, "Faculty", 5)
    assert mix_of(with_msmes)["check"]["within"] is False


def test_an_added_category_counts_as_other(make_activity, register) -> None:
    ParticipantCategory.objects.create(name="Students")
    activity = make_activity(status=ActivityStatus.SCHEDULED, categories=("Faculty", "Incubatees", "Students"))
    people(register, activity, "Students", 1)
    people(register, activity, "Incubatees", 1)
    rows = {row["group"]: row for row in mix_of(activity)["rows"]}
    assert rows["Other categories"]["registered"] == 1


def test_needs_attention_warns_without_blocking_registration(make_activity, register) -> None:
    scheduled = make_activity(status=ActivityStatus.SCHEDULED, categories=MIXED, start=date(2030, 3, 10))
    people(register, scheduled, "Faculty", 5)
    people(register, scheduled, "Incubatees", 5)
    items = [item for item in attention.activity_attention(scheduled) if "Participant mix" in item.text]
    assert [(item.text, item.informational, item.tab) for item in items] == [(
        "Participant mix is outside NAVIGATU's guideline: Faculty 50% (at most 30%); Incubatees and MSMEs 50% "
        "(at least 70%).", False, "overview")]
    conducted = make_activity(status=ActivityStatus.CONDUCTED, categories=MIXED)
    people(register, conducted, "Faculty", 2, attended=True)
    (item,) = [item for item in attention.activity_attention(conducted) if "Participant mix" in item.text]
    assert item.informational  # shown, never counted, once nothing can change


def test_registration_is_never_blocked_by_the_mix() -> None:
    import inspect

    from registrations import services

    assert "participant_mix" not in inspect.getsource(services)


def test_mix_chart_lays_out_bars_table_guideline_and_status(make_activity, register) -> None:
    activity = make_activity(status=ActivityStatus.SCHEDULED, categories=MIXED)
    people(register, activity, "Faculty", 1)
    people(register, activity, "Incubatees", 3)
    chart = mix_chart(mix_of(activity))
    assert chart["guideline"].startswith("NAVIGATU's guideline: Faculty at most 30%, Incubatees and MSMEs at least 70%")
    assert [bar["label"] for bar in chart["bars"]] == ["Registered", "Attended"]
    assert chart["bars"][0]["aria_label"] == "Registered: 4 in total: Faculty 1 (25%), Incubatees 3 (75%), MSMEs 0 (0%)"
    assert chart["status"] == {"within": True, "text": "Within the guideline (registrations, nobody is marked as attended yet)."}


def test_overview_and_dashboard_show_the_mix(director, login, make_activity, register) -> None:
    activity = make_activity(categories=MIXED, start=date(2026, 3, 10))
    people(register, activity, "Faculty", 2, attended=True)
    people(register, activity, "Incubatees", 2, attended=True)
    client = login(director)
    overview = client.get(reverse("activities:detail", args=[activity.pk])).content.decode()
    assert "Participant mix" in overview and "Outside the guideline (attendees): Faculty 50% (at most 30%)" in overview
    dashboard = client.get(reverse("home"), {"range": "custom", "start": "2026-01-01", "end": "2026-12-31"}).content.decode()
    assert "Participant mix" in dashboard and "1 of 1 checked activity is outside the guideline" in dashboard


def test_period_mix_adds_up_the_period_activities(make_activity, register) -> None:
    one = make_activity(categories=MIXED, start=date(2026, 3, 10))
    two = make_activity(categories=("Faculty",), start=date(2026, 4, 10))
    make_activity(categories=MIXED, start=date(2025, 4, 10))  # outside the period
    people(register, one, "Incubatees", 3, attended=True)
    people(register, two, "Faculty", 1, attended=True)
    mix = metrics.period_participant_mix(metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31)))
    assert (mix["attended"], mix["activities_checked"], mix["activities_outside"]) == (4, 1, 0)
    assert mix["check"] is None  # the guideline is shown next to the total, not checked on it
