"""For payment per activity (ASSUMPTIONS 463, owner feedback Part 2 item 13): one card per Conducted activity with its
OWN missing payment documents (never grouped by type), "Generate" for the files the system can make (Attendance Sheet,
Confirmation of Attendance, Activity Report; the Evaluation Summary is NOT a payment document) and "Upload" for the
others; shown on the Dashboard card, the Documents page "For payment" view and the activity's payment group. The
Dashboard's Needs attention keeps ONE line for all of them. The fixed clock keeps every date independent of today."""

from datetime import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from activities.models import ActivityStatus
from documents import services
from documents.models import DocumentType
from reports import dashboard_page, hub

from .conftest import JPG_BYTES, make_file

from .test_payment_documents import AFTER_DUE, NEW_END, _approved, client_for, payment_types

pytestmark = pytest.mark.django_db

PAYMENT_NAMES = {"Activity Report", "Attendance Sheet", "Confirmation of Attendance", "Event Photos"}


@pytest.fixture(autouse=True)
def fixed_clock(clock):
    clock(AFTER_DUE)


def _names(row) -> set[str]:
    return {item.document_type.name for item in row.missing}


def test_one_entry_per_conducted_activity_with_its_own_missing_documents(director, make_activity) -> None:
    first = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="Pitching Workshop")
    second = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="Mentoring Day")
    make_activity(ActivityStatus.ONGOING, start=NEW_END, end=NEW_END, title="Still Ongoing")  # never listed (425)
    _approved(director, first, "Attendance Sheet")
    rows = services.payment_missing(AFTER_DUE)
    assert [row.activity for row in rows] == [first, second]  # oldest due first, ties by ID
    assert _names(rows[0]) == PAYMENT_NAMES - {"Attendance Sheet"}  # approved = no longer missing
    assert _names(rows[1]) == PAYMENT_NAMES
    assert all(row.overdue for row in rows)
    assert "Evaluation Summary" not in _names(rows[0])


def test_an_activity_with_every_payment_document_approved_is_not_listed(director, make_activity) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    confirmation = DocumentType.objects.get(name="Confirmation of Attendance")
    types = {**payment_types(), "Confirmation of Attendance": confirmation}
    for name in PAYMENT_NAMES:
        upload = make_file("photo.jpg", JPG_BYTES) if name == "Event Photos" else make_file("file.pdf")
        document = services.upload_document(director, activity, types[name], upload)
        if document.status.status_name != "Approved":
            services.approve_document(director, document)
    assert services.payment_missing(AFTER_DUE) == []


def test_generate_for_files_the_system_makes_upload_for_the_others(make_activity) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    actions = {item.document_type.name: hub.payment_action(item.document_type, activity)
               for item in services.payment_missing(AFTER_DUE)[0].missing}
    for name in ("Attendance Sheet", "Confirmation of Attendance", "Activity Report"):
        assert actions[name]["label"] == "Generate"
        assert actions[name]["url"].startswith(reverse("reports:list") + "?kind=")
        assert f"&activity={activity.pk}" in actions[name]["url"]
    assert actions["Activity Report"]["url"].endswith(f"?kind=activity_report&activity={activity.pk}")
    assert actions["Event Photos"]["label"] == "Upload"
    assert actions["Event Photos"]["url"].startswith(f"/activities/{activity.pk}/?tab=documents&type=")


def test_documents_page_for_payment_view_shows_one_card_per_activity(director, make_activity) -> None:
    first = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="Pitching Workshop")
    second = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="Mentoring Day")
    html = client_for(director).get(reverse("documents:list") + "?view=payment").content.decode()
    assert "data-payment-cards" in html
    assert f'data-payment-activity="{first.pk}"' in html and f'data-payment-activity="{second.pk}"' in html
    assert html.count('data-payment-action="generate"') == 6 and html.count('data-payment-action="upload"') == 2


def test_documents_page_for_payment_view_when_nothing_is_missing(director) -> None:
    html = client_for(director).get(reverse("documents:list") + "?view=payment").content.decode()
    assert "Nothing missing for payment" in html


def test_dashboard_card_lists_the_activities(director, make_activity) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    card = dashboard_page.payment_card(director, AFTER_DUE)
    assert card["payment_total"] == 1 and card["payment"][0]["activity"] == activity
    assert {row["item"].document_type.name for row in card["payment"][0]["items"]} == PAYMENT_NAMES
    html = client_for(director).get(reverse("home")).content.decode()
    assert "data-payment-card" in html and f'data-payment-activity="{activity.pk}"' in html


def test_dashboard_card_shows_at_most_four_activities(director, make_activity) -> None:
    for number in range(dashboard_page.PAYMENT_SHOWN + 1):
        make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title=f"Workshop {number}")
    card = dashboard_page.payment_card(director, AFTER_DUE)
    assert len(card["payment"]) == dashboard_page.PAYMENT_SHOWN
    assert card["payment_total"] == dashboard_page.PAYMENT_SHOWN + 1


def test_needs_attention_has_one_payment_line_for_every_activity(director, make_activity) -> None:
    make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="Pitching Workshop")
    make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="Mentoring Day")
    page = dashboard_page.build_dashboard(director, {}, AFTER_DUE)
    payment_url = f"{reverse('documents:list')}?view=payment"
    lines = [item for item in page.attention if item.action_url == payment_url]
    assert len(lines) == 1 and "2 activities are missing payment documents" in lines[0].title
    assert not any(name in item.title for item in page.attention for name in PAYMENT_NAMES)  # never by type


def test_activity_payment_group_has_generate_buttons(director, make_activity) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    html = client_for(director).get(
        f"{reverse('activities:detail', args=[activity.pk])}?tab=documents").content.decode()
    assert html.count('data-payment-action="generate"') == 3
    assert f"?kind=activity_report&amp;activity={activity.pk}" in html


def test_an_approved_payment_file_has_no_generate_button(director, make_activity) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    _approved(director, activity, "Activity Report")
    html = client_for(director).get(
        f"{reverse('activities:detail', args=[activity.pk])}?tab=documents").content.decode()
    assert html.count('data-payment-action="generate"') == 2


def test_before_conducted_nothing_is_listed(make_activity) -> None:
    make_activity(ActivityStatus.ONGOING, start=NEW_END, end=NEW_END)
    later = timezone.make_aware(datetime(2027, 1, 30, 9, 0))
    assert services.payment_missing(later) == []
