"""Participant mix and NAVIGATU's guideline (ASSUMPTIONS 424, confirmed 2026-11-28): Faculty at most 30 % and Incubatees
at most 70 % of ALL counted participants; MSMEs and other categories are in neither share (only in the total); checked
only for activities open to Faculty and to Incubatees or MSMEs; attendees once anybody attended, else registrations; a
warning in Needs attention, never a block."""

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
    (3, 7, 10, True),  # exactly 30 % and 70 %: both are maximums, so inside
    (4, 6, 10, False),
    (0, 10, 10, False),  # 100 % Incubatees: over the 70 % cap
    (0, 7, 10, True),
    (2, 5, 10, True),  # 3 MSMEs: in neither share, only in the total
    (2, 6, 10, True),  # Incubatees are no longer a minimum
])
def test_mix_check_boundaries(faculty, incubatees, total, within) -> None:
    assert metrics.mix_check(faculty, incubatees, total)["within"] is within


def test_mix_check_explains_each_problem_and_skips_an_empty_mix() -> None:
    assert metrics.mix_check(0, 0, 0) is None
    assert metrics.mix_check(2, 8, 10)["problems"] == ["Incubatees 80% (at most 70%)"]
    assert metrics.mix_check(4, 6, 10)["problems"] == ["Faculty 40% (at most 30%)"]


# NAVIGATU's examples (2026-11-28), counted through the real registrations: (Faculty, Incubatees, MSMEs) -> problems.
@pytest.mark.parametrize(("faculty", "incubatees", "msmes", "problems"), [
    (3, 7, 0, []),
    (2, 8, 0, ["Incubatees 80% (at most 70%)"]),
    (3, 6, 1, []),
    (2, 5, 3, []),
    (4, 6, 0, ["Faculty 40% (at most 30%)"]),
    (4, 7, 0, ["Faculty 36% (at most 30%)"]),  # 4/11 Faculty, 7/11 = 64 % Incubatees: only the Faculty cap
    (5, 10, 0, ["Faculty 33% (at most 30%)"]),
    (1, 8, 0, ["Incubatees 89% (at most 70%)"]),
])
def test_navigatus_examples(make_activity, register, faculty, incubatees, msmes, problems) -> None:
    activity = make_activity(status=ActivityStatus.SCHEDULED, categories=MIXED)
    people(register, activity, "Faculty", faculty)
    people(register, activity, "Incubatees", incubatees)
    people(register, activity, "MSMEs", msmes)
    assert mix_of(activity)["check"] == {"within": not problems, "problems": problems}


def test_both_problems_are_listed_when_both_caps_are_exceeded() -> None:
    """30 % + 70 % = 100 %, so real counts never exceed both caps; the check still lists both when given such numbers."""
    assert metrics.mix_check(4, 8, 10)["problems"] == ["Faculty 40% (at most 30%)", "Incubatees 80% (at most 70%)"]


def test_msmes_are_in_neither_share(make_activity, register) -> None:
    activity = make_activity(status=ActivityStatus.SCHEDULED, categories=MIXED)
    people(register, activity, "Faculty", 3)
    people(register, activity, "Incubatees", 2)
    people(register, activity, "MSMEs", 5)
    mix = mix_of(activity)
    assert mix["basis"] == "registered" and mix["check"] == {"within": True, "problems": []}
    rows = {row["group"]: row for row in mix["rows"]}
    assert (rows["Faculty"]["registered"], rows["Faculty"]["registered_share"]) == (3, 0.3)
    assert rows["MSMEs"]["registered_share"] == 0.5 and "Other categories" not in rows
    only_msmes = make_activity(status=ActivityStatus.SCHEDULED, categories=MIXED)
    people(register, only_msmes, "MSMEs", 9)
    people(register, only_msmes, "Faculty", 1)
    assert mix_of(only_msmes)["check"]["within"] is True  # 10 % Faculty, 0 % Incubatees: no minimum any more


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
        "Participant mix is outside NAVIGATU's guideline: Faculty 50% (at most 30%).", False, "overview")]
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
    people(register, activity, "Incubatees", 2)
    people(register, activity, "MSMEs", 1)
    chart = mix_chart(mix_of(activity))
    assert chart["guideline"] == ("NAVIGATU's guideline: Faculty at most 30% and Incubatees at most 70% of the participants "
                                  "of an activity; MSMEs and other participants are not counted in either share.")
    assert [bar["label"] for bar in chart["bars"]] == ["Registered", "Attended"]
    assert chart["bars"][0]["aria_label"] == "Registered: 4 in total: Faculty 1 (25%), Incubatees 2 (50%), MSMEs 1 (25%)"
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
    people(register, one, "Incubatees", 2, attended=True)
    people(register, one, "MSMEs", 1, attended=True)  # 67 % Incubatees: within (all Incubatees would be outside)
    people(register, two, "Faculty", 1, attended=True)
    mix = metrics.period_participant_mix(metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31)))
    assert (mix["attended"], mix["activities_checked"], mix["activities_outside"]) == (4, 1, 0)
    assert mix["check"] is None  # the guideline is shown next to the total, not checked on it
