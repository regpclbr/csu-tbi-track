"""Django's {# ... #} comment works on ONE line only: a {# that is closed on a later line is printed on the page as text
(the bug on Reports -> New report, ASSUMPTIONS 384). Multi-line comments must use {% comment %} ... {% endcomment %}.

Two checks: every template file (no {# without its #} on the same line), and the rendered staff pages (no "{#" or "#}"
in the HTML, with the AI on and off)."""

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory
from assistant import gemini

TEMPLATE_DIRS = [Path(settings.BASE_DIR) / "templates", *Path(settings.BASE_DIR).glob("*/templates")]
UNCLOSED = re.compile(r"\{#(?:(?!#\}).)*$")  # a {# with no #} after it on the same line


def test_no_template_opens_a_comment_it_does_not_close_on_the_same_line() -> None:
    problems = []
    for folder in TEMPLATE_DIRS:
        for path in folder.rglob("*.html"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if UNCLOSED.search(line):
                    problems.append(f"{path.relative_to(settings.BASE_DIR)}:{number}: {line.strip()[:80]}")
    assert problems == [], "Use {% comment %} ... {% endcomment %} for comments over several lines:\n" + "\n".join(problems)


def _activity(member) -> Activity:
    deadline = timezone.make_aware(datetime(2026, 11, 1, 17, 0))
    activity = Activity.objects.create(
        created_by=member, status=ActivityStatus.objects.get(name=ActivityStatus.DRAFT),
        activity_type=ActivityType.objects.get(name="Workshop"), title="Pitching Workshop",
        start_date=date(2026, 11, 5), end_date=date(2026, 11, 6), start_time=time(8, 0), end_time=time(17, 0),
        venue="CSU Hall", max_slots=30, registration_deadline=deadline, evaluation_deadline=deadline + timedelta(days=14),
        budget_amount=Decimal("5000.00"),
    )
    activity.participant_categories.add(ParticipantCategory.objects.order_by("pk").first())
    return activity


def _staff_pages(activity: Activity) -> list[str]:
    tabs = ["overview", "history", "forms", "registrations", "evaluation", "documents", "certificates"]
    return [
        reverse("home"), reverse("activities:list"), reverse("activities:create"), reverse("activities:edit", args=[activity.pk]),
        *[f"{reverse('activities:detail', args=[activity.pk])}?tab={tab}" for tab in tabs],
        reverse("documents:list"), f"{reverse('documents:list')}?view=all", reverse("certificates:list"),
        f"{reverse('certificates:list')}?view=all", reverse("certificates:signatories"), reverse("certificates:text"),
        reverse("reports:list"), reverse("assistant:chat"), reverse("notifications:list"),
        f"{reverse('search')}?q=pitch",
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("ai_on", [True, False], ids=["ai-on", "ai-off"])
def test_no_rendered_staff_page_shows_comment_marks(make_member, monkeypatch, ai_on) -> None:
    if not ai_on:
        monkeypatch.setattr(gemini, "_override", None)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    director, admin = make_member("Director"), make_member("System Administrator")
    activity = _activity(director)
    pages = [(director, url) for url in _staff_pages(activity)]
    pages += [(admin, reverse(name)) for name in ("accounts:console", "accounts:console_roles", "accounts:console_certificates",
                                                  "accounts:console_system")]
    clients: dict[int, Client] = {}
    for member, url in pages:
        client = clients.setdefault(member.pk, Client())
        client.force_login(member)
        response = client.get(url)
        assert response.status_code == 200, url
        html = response.content.decode()
        assert "{#" not in html and "#}" not in html, f"a template comment is printed on {url}"
