"""ASSUMPTIONS 372: the Certificates page (/certificates/) organised by activity (the default) or as one list, newest
first, with the search. The ordering, grouping and counts are services.certificates_by_activity /
list_certificates_newest / search_q; these tests check them and that the page shows them in both views."""

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from activities.models import Activity, ActivityResourcePerson, ActivityStatus, ActivityType, ResourcePerson
from certificates import services
from certificates.models import Certificate

from .conftest import S, certificate_of

pytestmark = pytest.mark.django_db

URL = reverse("certificates:list")
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


@pytest.fixture
def make_activity(director):
    """make_activity("Title", end=date, end_time=time | None): a Conducted activity (one day unless start is given)."""
    status, _ = ActivityStatus.objects.get_or_create(name="Conducted")
    kind, _ = ActivityType.objects.get_or_create(name="Workshop")

    def make(title: str, end: date, end_time: time | None = time(17, 0), start: date | None = None) -> Activity:
        deadline = timezone.make_aware(datetime(2026, 1, 1, 17, 0))
        return Activity.objects.create(
            created_by=director, status=status, activity_type=kind, title=title,
            start_date=start or end, end_date=end,
            start_time=time(8, 0) if end_time else None, end_time=end_time,
            venue="Demo Hall", max_slots=30, registration_deadline=deadline,
            evaluation_deadline=deadline + timedelta(days=14), budget_amount=Decimal("0"),
        )

    return make


@pytest.fixture
def make_certificates(design):
    """make_certificates(activity, count, status=..., delivery=...): resource-person certificates numbered in creation
    order (a higher number = newer), with fake @demo.example.com people."""
    template, vp, head = design
    counter = {"n": 0}

    def make(activity: Activity, count: int = 1, status: str = S.GENERATED, delivery: str = "hard_copy",
             name: str | None = None) -> list[Certificate]:
        made = []
        for _ in range(count):
            counter["n"] += 1
            person = ResourcePerson.objects.create(name=name or f"Demo Speaker {counter['n']}",
                                                   email=f"speaker{counter['n']}@demo.example.com")
            ActivityResourcePerson.objects.create(activity=activity, resource_person=person, topic="Demo")
            made.append(Certificate.objects.create(
                certificate_template=template, certificate_number=f"TBI-2026-{counter['n']:05d}",
                recipient_type=Certificate.RecipientType.RESOURCE_PERSON, resource_person=person, activity=activity,
                right_signatory=vp, left_signatory=head, status=status, delivery_method=delivery,
                issued_at=timezone.now() if status == S.ISSUED else None,
            ))
        return made

    return make


def titles(result) -> list[str]:
    return [entry.activity.title for entry in result.items]


def numbers(certificates) -> list[str]:
    return [certificate.certificate_number for certificate in certificates]


# --- Ordering and grouping (services) -----------------------------------------------------------------------------


def test_newest_activity_first_by_end_date_then_end_time(make_activity, make_certificates) -> None:
    """END date newest first; the same end date: the later end time first; no times = the whole day (ends at midnight),
    so before the timed ones that day; a multi-day activity counts by its end."""
    old = make_activity("Old", date(2026, 3, 2))
    morning = make_activity("Morning", date(2026, 6, 10), time(12, 0))
    afternoon = make_activity("Afternoon", date(2026, 6, 10), time(17, 0))
    whole_day = make_activity("Whole day", date(2026, 6, 10), None)
    long_one = make_activity("Started earlier, ends last", date(2026, 7, 1), start=date(2026, 5, 1))
    for activity in (old, morning, afternoon, whole_day, long_one):
        make_certificates(activity)
    result = services.certificates_by_activity()
    assert titles(result) == ["Started earlier, ends last", "Whole day", "Afternoon", "Morning", "Old"]


def test_newest_certificate_first_inside_a_group(make_activity, make_certificates) -> None:
    activity = make_activity("Demo Day", date(2026, 6, 1))
    first = make_certificates(activity, 3)
    other = make_activity("Other", date(2026, 5, 1))
    make_certificates(other, 1)
    later = make_certificates(activity, 2)
    entry = services.certificates_by_activity().items[0]
    assert entry.activity == activity
    assert numbers(entry.certificates) == numbers(reversed(first + later))
    assert entry.counts == {S.GENERATED: 5, S.APPROVED: 0, S.ISSUED: 0}


def test_flat_list_is_newest_number_first(make_activity, make_certificates) -> None:
    a = make_activity("A", date(2026, 6, 1))
    b = make_activity("B", date(2026, 1, 1))
    made = make_certificates(a, 2) + make_certificates(b, 2) + make_certificates(a, 1)
    assert numbers(services.list_certificates_newest().items) == numbers(reversed(made))


def test_pages_count_activities_and_never_split_one(make_activity, make_certificates) -> None:
    """10 activities a page; an activity with more certificates than a flat page (20) stays whole on its page."""
    big = make_activity("Big", date(2026, 12, 1))
    make_certificates(big, 25)
    for day in range(1, 13):
        make_certificates(make_activity(f"Small {day:02d}", date(2026, 10, day)), 2)
    first = services.certificates_by_activity(page=1)
    second = services.certificates_by_activity(page=2)
    assert (first.total, first.num_pages) == (13, 2)
    assert len(first.items) == 10 and len(second.items) == 3
    assert first.items[0].activity == big and len(first.items[0].certificates) == 25
    assert titles(first)[-1] == "Small 04" and titles(second) == ["Small 03", "Small 02", "Small 01"]
    every = [entry.activity.pk for entry in first.items + second.items]
    assert len(every) == len(set(every)) == 13  # each activity on exactly one page


def test_filters_hide_empty_groups_and_counts_leave_out_only_the_status(make_activity, make_certificates) -> None:
    a = make_activity("A", date(2026, 6, 1))
    b = make_activity("B", date(2026, 5, 1))
    make_certificates(a, 2, status=S.ISSUED)
    make_certificates(a, 1, status=S.GENERATED)
    make_certificates(b, 2, status=S.GENERATED)
    result = services.certificates_by_activity({"status": S.ISSUED})
    assert titles(result) == ["A"]  # B has no Issued certificate: hidden
    assert len(result.items[0].certificates) == 2
    assert result.items[0].counts == {S.GENERATED: 1, S.APPROVED: 0, S.ISSUED: 2}
    assert titles(services.certificates_by_activity({"activity": b.pk})) == ["B"]  # one activity chosen: only its group
    assert titles(services.certificates_by_activity({"delivery_method": "email"})) == []


def test_search_matches_number_names_email_and_activity(make_activity, make_certificates) -> None:
    a = make_activity("Pitch Clinic", date(2026, 6, 1))
    b = make_activity("Founders Night", date(2026, 5, 1))
    target = make_certificates(a, 1, name="Lorna Demo")[0]
    make_certificates(b, 2)
    assert numbers(services.list_certificates_newest({"q": "lorna"}).items) == [target.certificate_number]
    assert numbers(services.list_certificates_newest({"q": target.certificate_number}).items) == [target.certificate_number]
    assert services.list_certificates_newest({"q": "founders night"}).total == 2  # every word, any case
    assert services.list_certificates_newest({"q": "founders lorna"}).total == 0
    assert titles(services.certificates_by_activity({"q": "lorna"})) == ["Pitch Clinic"]


def test_search_finds_the_name_and_email_typed_in_the_evaluation(generated, evaluate) -> None:
    activity, people = generated
    evaluate(people[0], name="Printed Demo Name", email="printed@demo.example.com")
    expected = [certificate_of(people[0]).certificate_number]
    assert numbers(services.list_certificates_newest({"q": "printed demo"}).items) == expected
    assert numbers(services.list_certificates_newest({"q": "printed@demo.example"}).items) == expected
    registered = services.list_certificates_newest({"q": people[1].name.split()[0]}).items  # the registered name too
    assert certificate_of(people[1]).certificate_number in numbers(registered)


# --- The page --------------------------------------------------------------------------------------------------------


def test_grouped_view_is_the_default_with_headers(director, login, make_activity, make_certificates) -> None:
    newer = make_activity("Newer Demo Activity", date(2026, 9, 5), start=date(2026, 9, 4))
    older = make_activity("Older Demo Activity", date(2026, 8, 1))
    make_certificates(newer, 2, status=S.GENERATED)
    made = make_certificates(older, 2, status=S.ISSUED)
    Activity.objects.filter(pk=older.pk).update(certificates_approved_by=director,
                                                certificates_approved_at=timezone.make_aware(datetime(2026, 8, 2, 15, 5)))
    response = login(director).get(URL)
    html = response.content.decode()
    groups = response.context["groups"]
    assert [group["activity"].title for group in groups] == ["Newer Demo Activity", "Older Demo Activity"]
    assert html.index("Newer Demo Activity") < html.index("Older Demo Activity")
    assert f'href="{reverse("activities:detail", args=[newer.pk])}?tab=certificates"' in html
    assert "Sep 4–5, 2026" in html and "8:00 AM" in html
    assert "Needs approval" in html and f"Approved by {director.name} on August 2, 2026, 3:05 PM" in html
    assert "2 activities · 4 certificates · most recent activity first" in html
    assert 'aria-current="true"' in html and "By activity (recommended)" in html
    # No Activity column inside a group; the newest certificate first.
    assert ">Activity</th>" not in html
    assert html.index(made[1].certificate_number) < html.index(made[0].certificate_number)
    assert [group["counts"][2]["count"] for group in groups] == [0, 2]  # Issued


def test_flat_view_keeps_the_activity_column(director, login, make_activity, make_certificates) -> None:
    a = make_activity("Demo A", date(2026, 9, 1))
    made = make_certificates(a, 3)
    response = login(director).get(URL, {"view": "all"})
    html = response.content.decode()
    assert ">Activity</th>" in html and response.context["groups"] == []
    assert html.index(made[2].certificate_number) < html.index(made[0].certificate_number)
    assert "3 certificates · newest first" in html
    # The view stays in the links: pager, counts, Clear.
    page = login(director).get(URL, {"view": "all", "status": S.GENERATED}).content.decode()
    assert "view=all&amp;status=generated" in page or "status=generated&amp;view=all" in page
    assert re.search(r'href="\?view=all"[^>]*class="btn btn-ghost btn-sm">Clear', page)


def test_unknown_view_falls_back_and_htmx_gets_only_the_results(director, login, make_activity, make_certificates) -> None:
    make_certificates(make_activity("Demo", date(2026, 9, 1)))
    response = login(director).get(URL, {"view": "cards"})
    assert response.context["filters"].view == "activity" and len(response.context["groups"]) == 1
    partial = login(director).get(URL, {"view": "all"}, **HTMX)
    assert [t.name for t in partial.templates][0] == "certificates/_results.html"
    assert "layouts/app.html" not in [t.name for t in partial.templates]
    assert 'id="cert-q"' in partial.content.decode()  # the filters are swapped with the results


@pytest.mark.parametrize("view", ["activity", "all"])
def test_filters_counts_and_search_work_in_both_views(view, director, login, make_activity, make_certificates) -> None:
    a = make_activity("Alpha Demo", date(2026, 9, 1))
    b = make_activity("Beta Demo", date(2026, 8, 1))
    issued = make_certificates(a, 1, status=S.ISSUED, name="Issued Person")[0]
    waiting = make_certificates(a, 1, status=S.GENERATED, delivery="email", name="Waiting Person")[0]
    other = make_certificates(b, 1, status=S.GENERATED, name="Beta Person")[0]
    client = login(director)

    def shown(**params):
        """(certificate numbers shown, in order; the page's HTML)."""
        response = client.get(URL, {"view": view, **params}, **HTMX)
        context = response.context
        rows = context["rows"] if view == "all" else [row for group in context["groups"] for row in group["rows"]]
        assert bool(context["groups"]) == (view == "activity" and bool(rows))
        return [row["certificate"].certificate_number for row in rows], response.content.decode()

    listed, html = shown(status=S.ISSUED)
    assert listed == [issued.certificate_number]
    assert re.search(r'Waiting for approval</span>\s*<span class="font-semibold text-ink">2</span>', html)  # counts ignore the status
    assert shown(activity=b.pk)[0] == [other.certificate_number]
    listed, html = shown(recipient_type="registration")
    assert listed == [] and "No certificates match the filters" in html
    assert shown(delivery_method="email")[0] == [waiting.certificate_number]
    listed, html = shown(q="beta person")
    assert listed == [other.certificate_number] and 'value="beta person"' in html
    # Both views order the matches: newest number first, or newest activity first and then newest number first.
    expected = [other, waiting, issued] if view == "all" else [waiting, issued, other]
    assert shown(q="person")[0] == numbers(expected)


def test_view_only_member_sees_the_same_view_only_page(view_only, director, login, make_activity, make_certificates) -> None:
    make_certificates(make_activity("Demo", date(2026, 9, 1)), 2)
    for member in (view_only, director):
        response = login(member).get(URL)
        html = response.content.decode()
        assert response.status_code == 200 and "Demo" in html and "Needs approval" in html
        assert "Approve certificates for this activity" not in html and "/certificates/approve/" not in html
        assert login(member).get(URL, {"view": "all"}).status_code == 200


@pytest.mark.parametrize("view", ["activity", "all"])
def test_queries_do_not_grow_with_the_number_of_activities(view, director, login, make_activity, make_certificates) -> None:
    def count_queries() -> int:
        client = login(director)
        with CaptureQueriesContext(connection) as queries:
            assert client.get(URL, {"view": view}).status_code == 200
        return len(queries)

    for day in range(1, 4):
        activity = make_activity(f"Demo {day}", date(2026, 9, day))
        make_certificates(activity, 2, status=S.GENERATED)
        make_certificates(activity, 1, status=S.ISSUED)
    few = count_queries()
    for day in range(4, 10):
        activity = make_activity(f"Demo {day}", date(2026, 9, day))
        make_certificates(activity, 2, status=S.GENERATED)
        make_certificates(activity, 1, status=S.ISSUED)
    Activity.objects.filter(title="Demo 5").update(certificates_approved_by=director, certificates_approved_at=timezone.now())
    many = count_queries()
    assert many == few, (few, many)


# --- The approval state in the group header (ASSUMPTIONS 373) --------------------------------------------------------


def test_group_header_shows_what_the_user_needs_to_do(director, view_only, login, make_activity,
                                                      make_certificates) -> None:
    """Not approved + waiting = "Needs approval" (orange); every certificate Issued and not approved (the owner's "Test"
    activity) = "All certificates issued (N)" (the Issued badge's green) + the short note; old data (some Approved,
    nothing waiting) = the grey note; approved = "Approved by … on …". Admin Staff sees the same."""
    waiting = make_activity("Waiting Demo", date(2026, 9, 4))
    make_certificates(waiting, 1, status=S.GENERATED)
    issued_only = make_activity("Issued Demo", date(2026, 9, 3))
    make_certificates(issued_only, 2, status=S.ISSUED)
    mixed = make_activity("Mixed Demo", date(2026, 9, 2))
    make_certificates(mixed, 1, status=S.APPROVED)
    make_certificates(mixed, 1, status=S.ISSUED)
    done = make_activity("Approved Demo", date(2026, 9, 1))
    make_certificates(done, 1, status=S.ISSUED)
    Activity.objects.filter(pk=done.pk).update(certificates_approved_by=director, certificates_approved_at=timezone.now())
    for member in (director, view_only):
        response = login(member).get(URL)
        groups = {group["activity"].title: group for group in response.context["groups"]}
        states = {title: (group["approval_state"], group["approval"]) for title, group in groups.items()}
        assert states["Waiting Demo"] == ("needs_approval", "Needs approval")
        assert states["Issued Demo"] == ("all_issued", "All certificates issued (2)")
        assert groups["Issued Demo"]["approval_note"] == services.ALL_ISSUED_NOTE
        assert states["Mixed Demo"] == ("not_approved", "Not approved — new certificates will wait for approval")
        assert states["Approved Demo"][0] == "approved"
        assert states["Approved Demo"][1].startswith(f"Approved by {director.name}")
        html = response.content.decode()
        assert html.count("Needs approval") == 1 and html.count(services.ALL_ISSUED_NOTE) == 1
        assert re.search(r'text-green-800" data-approval-state="all_issued"', html)
        assert re.search(r'text-slate-600" data-approval-state="not_approved"', html)
        assert re.search(r'text-amber-800" data-approval-state="needs_approval"', html)
        assert "Approve certificates for this activity" not in html


def test_group_header_counts_every_certificate_whatever_the_filters(director, login, make_activity,
                                                                    make_certificates, generated) -> None:
    """The state uses ALL the activity's certificates: filtering to the participants (here all Issued) still shows
    "Needs approval" while the resource person's certificate waits; once it is Issued too, "All certificates issued (3)"
    even when a filter shows fewer rows."""
    activity, _ = generated
    Certificate.objects.filter(activity=activity, recipient_type="registration").update(status=S.ISSUED)
    response = login(director).get(URL, {"recipient_type": "registration"})
    (group,) = response.context["groups"]
    assert [row["certificate"].status for row in group["rows"]] == [S.ISSUED, S.ISSUED]
    assert group["approval_state"] == "needs_approval"
    Certificate.objects.filter(activity=activity).update(status=S.ISSUED)
    (group,) = login(director).get(URL, {"recipient_type": "registration"}).context["groups"]
    assert len(group["rows"]) == 2
    assert (group["approval_state"], group["approval"]) == ("all_issued", "All certificates issued (3)")
