"""The draft Request Letter and draft Activity Design in the report generator (ASSUMPTIONS 447): made from a Draft or
Scheduled activity or from typed details (no activity, no period: the only kinds allowed so), every date, name and
amount from the activity or the member's typing, NEVER saved to the activity's documents (not from the generator and
not from Approvals), submit allowed. Fake Gemini and a fake renderer only; no test depends on today's date."""

import io
import json
from datetime import date, time
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from accounts.models import TBIMember
from activities import budget as activity_budget
from activities.models import ActivityStatus, BudgetCategory, BudgetLine, FundSource
from approvals import services as approval_services
from approvals.models import Submission
from documents.models import Document
from reports import draft_export, draft_facts, generator
from reports.models import ReportDraft, ReportDraftVersion

from .conftest import TEST_NOW
from .test_generator import renderer, reply, staff, workshop  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db
Kind = ReportDraft.Kind


@pytest.fixture
def meals(roles) -> BudgetCategory:
    return BudgetCategory.objects.get_or_create(name="Meals/Snacks")[0]


@pytest.fixture
def raf(roles) -> FundSource:
    return FundSource.objects.get(code="RAF")


def typed(meals, raf, **changes) -> dict:
    details = {
        "title": "Design Thinking Bootcamp", "start_date": "2027-02-10", "end_date": "2027-02-11",
        "start_time": "08:00", "end_time": "17:00", "venue": "CSU Innovation Hub",
        "participants": "30 incubatees and MSMEs, 5 faculty", "objectives": "Teach design thinking\n\n Build prototypes ",
        "description": "A two-day bootcamp.", "fund_source": str(raf.pk),
        "budget_lines": [{"category": str(meals.pk), "particulars": "Lunch", "quantity": "35", "unit": "pax",
                          "days": "2", "unit_price": "150"},
                         {"category": "", "particulars": "", "quantity": "", "unit": "", "days": "", "unit_price": ""}],
    }
    details.update(changes)
    return details


def letter(member, meals, raf, **extra) -> ReportDraft:
    options = {"memo_for_name": "Dr. Rolyn Daguil", "memo_for_title": "University President",
               "memo_thru_name": "Dr. Ana Santos", "memo_thru_title": "Vice President for RDIE"}
    return generator.create_draft(member, kind=Kind.REQUEST_LETTER, proposal=typed(meals, raf), options=options,
                                  now=TEST_NOW, **extra)


def write(draft, fake_gemini=None, **sections) -> ReportDraftVersion:
    if fake_gemini is not None:
        fake_gemini.replies.append(reply(**sections))
    version = generator.latest_version(draft)
    generator.generate_version(version.pk, now=TEST_NOW)
    version.refresh_from_db()
    return version


@pytest.fixture
def draft_activity(make_activity, meals, raf):
    """A Draft activity with budget lines and a fund source (an Activity Design is needed before Scheduled)."""
    activity = make_activity("Pitch Night", status=ActivityStatus.DRAFT, start=date(2026, 12, 3),
                             categories=("Incubatees", "MSMEs"))
    BudgetLine.objects.create(activity=activity, category=meals, particulars="Snacks", quantity=Decimal("40"),
                              unit="pax", days=1, unit_price=Decimal("75.50"), line_total=Decimal("3020.00"))
    activity.budget_amount, activity.fund_source, activity.objectives = Decimal("3020.00"), raf, "Pitch to investors"
    activity.save(update_fields=["budget_amount", "fund_source", "objectives"])
    return activity


# --- Without an activity ---------------------------------------------------------------------------------------------------


def test_a_draft_letter_without_an_activity_or_a_period(staff, meals, raf, fake_gemini) -> None:
    draft = letter(staff, meals, raf)
    assert draft.activity is None and draft.date_range_start is None and draft.date_range_end is None
    assert draft.title == "Request Letter – Design Thinking Bootcamp"
    stored = draft.options["proposal"]
    assert stored["objectives"] == "Teach design thinking\nBuild prototypes"  # tidied like ACTIVITY.objectives
    assert stored["budget_lines"] == [{"category": meals.pk, "particulars": "Lunch", "quantity": "35.00", "unit": "pax",
                                       "days": 2, "unit_price": "150.00"}]  # the empty row is skipped
    version = write(draft, fake_gemini, body=(
        "We request the conduct of [[title]] on [[dates]] at [[venue]] for [[participants]]. "
        "The budget of [[budget_total]] is charged to [[fund_source]]."))
    body = version.sections["body"]
    assert body["unverified"] == []
    # 35 pax x 2 days x 150.00 = 10,500.00 computed by activities.budget, never by the model.
    assert body["text"] == ("We request the conduct of Design Thinking Bootcamp on February 10–11, 2027 at CSU "
                            "Innovation Hub for 30 incubatees and MSMEs, 5 faculty. The budget of ₱10,500.00 is charged "
                            f"to {raf}.")
    memo = version.facts["memo"]
    assert memo["subject"] == "Request for the conduct of Design Thinking Bootcamp"
    assert memo["date"] == "October 23, 2026"  # the version's `now`, never today
    assert [block["label"] for block in version.facts["signatures"]] == ["Recommending Approval:", "Approved by:"]
    assert version.facts["signatures"][1]["name"] == "Dr. Rolyn Daguil"


def test_only_the_two_draft_kinds_may_have_neither_an_activity_nor_a_period(staff) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ReportDraft.objects.create(kind=Kind.OTHER, title="x", created_by=staff)
    for kind in generator.PROPOSAL_KINDS:
        ReportDraft.objects.create(kind=kind, title="x", created_by=staff)
    with pytest.raises(ValidationError):  # and the service never lets another kind through without a scope
        generator.create_draft(staff, kind=Kind.OTHER)


def test_typed_details_are_checked(staff, meals, raf) -> None:
    with pytest.raises(ValidationError) as error:
        letter_details = typed(meals, raf, title=" ", venue="", start_date="2027-02-12", end_date="2027-02-10",
                               start_time="8", end_time="", fund_source="999999",
                               budget_lines=[{"category": str(meals.pk), "particulars": "Lunch", "quantity": "-1",
                                              "unit": "pax", "days": "1", "unit_price": "150"}])
        generator.create_draft(staff, kind=Kind.ACTIVITY_DESIGN, proposal=letter_details)
    problems = error.value.message_dict
    for key in ("proposal_title", "proposal_venue", "proposal_end_date", "proposal_start_time", "proposal_fund_source",
                "budget-0-quantity"):
        assert key in problems, key
    with pytest.raises(ValidationError) as error:  # one day: the end time after the start time
        generator.create_draft(staff, kind=Kind.ACTIVITY_DESIGN, proposal=typed(
            meals, raf, end_date="2027-02-10", start_time="13:00", end_time="09:00"))
    assert "proposal_end_time" in error.value.message_dict


# --- From a Draft or Scheduled activity ---------------------------------------------------------------------------------


def test_only_draft_and_scheduled_activities(staff, draft_activity, workshop, make_activity) -> None:
    scheduled = make_activity("Scheduled One", status=ActivityStatus.SCHEDULED, start=date(2026, 12, 10))
    for activity in (draft_activity, scheduled):
        assert generator.create_draft(staff, kind=Kind.ACTIVITY_DESIGN, activity_id=activity.pk).activity == activity
    with pytest.raises(ValidationError) as error:  # Conducted
        generator.create_draft(staff, kind=Kind.REQUEST_LETTER, activity_id=workshop.pk)
    assert error.value.message_dict == {"activity": [generator.PROPOSAL_ACTIVITY]}
    with pytest.raises(ValidationError) as error:
        generator.create_draft(staff, kind=Kind.REQUEST_LETTER, start="2026-10-01", end="2026-12-31")
    assert error.value.message_dict == {"activity": [generator.NO_PERIOD_HERE]}
    from reports import generator_pages

    choices = generator_pages.proposal_activity_choices()
    assert [a.title for a in choices] == ["Pitch Night", "Scheduled One"]  # Draft first


def test_the_activity_design_budget_is_the_activitys_budget_table(staff, draft_activity, fake_gemini) -> None:
    version = write(generator.create_draft(staff, kind=Kind.ACTIVITY_DESIGN, activity_id=draft_activity.pk,
                                           options={"approved_by_name": "Dr. Rolyn Daguil",
                                                    "approved_by_title": "University President"}),
                    fake_gemini, rationale="MSMEs need investors.", expected_outputs="Ten pitches\nInvestor list")
    summary = activity_budget.budget_summary(draft_activity)
    budget = version.facts["budget"]
    assert [row["particulars"] for row in budget["rows"]] == [row["particulars"] for row in summary["rows"]] == ["Snacks"]
    assert budget["rows"][0]["quantity"] == summary["rows"][0]["quantity"] and budget["rows"][0]["unit_cost"] == "₱75.50"
    assert budget["total"] == "₱3,020.00" and budget["fund_source"] == summary["fund_source"]
    proposal = version.facts["proposal"]
    assert proposal["objectives"] == ["Pitch to investors"] and proposal["duration"] == "1 day"
    assert proposal["participants"] == "Incubatees, MSMEs (up to 100 participants)"
    assert proposal["funds"].startswith("₱3,020.00 – ")
    keys = [key for key, _, _ in generator.section_specs(Kind.ACTIVITY_DESIGN, version.facts)]
    assert keys == ["rationale", "expected_outputs"]  # objectives were given: the AI never writes them
    labels = [block["label"] for block in version.facts["signatures"]]
    assert labels == ["Prepared by:", "Recommending Approval:", "Approved by:"]


def test_the_ai_writes_objectives_only_when_none_were_given(staff, meals, raf, fake_gemini) -> None:
    draft = generator.create_draft(staff, kind=Kind.ACTIVITY_DESIGN, proposal=typed(meals, raf, objectives=""))
    version = write(draft, fake_gemini, rationale="Why.", objectives="Learn\nBuild", expected_outputs="Prototypes")
    assert "objectives" in version.sections and version.sections["objectives"]["ai"] is True
    blocks = draft_export.section_blocks(version)
    assert blocks["objectives"]["items"] == ["Learn", "Build"]


# --- Exports ----------------------------------------------------------------------------------------------------------------


def test_the_letter_and_the_design_open_as_docx_and_pdf(staff, meals, raf, draft_activity, fake_gemini, renderer) -> None:
    from docx import Document as Docx

    letter_version = write(letter(staff, meals, raf), fake_gemini, body="We request the conduct of [[title]].")
    data, _ = generator.export_docx(staff, letter_version)
    document = Docx(io.BytesIO(data))
    text = "\n".join(p.text for p in document.paragraphs)
    cells = "\n".join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
    assert "MEMORANDUM" in text and "We request the conduct of Design Thinking Bootcamp." in text
    for part in ("FOR:", "DR. ROLYN DAGUIL", "THRU:", "FROM:", "SUBJECT:", "Request for the conduct of Design Thinking "
                 "Bootcamp", "Recommending Approval:", "Approved by:"):
        assert part in cells, part
    assert generator.export_pdf(staff, letter_version)[0].startswith(b"%PDF-")
    html = renderer.rendered[-1]
    assert "MEMORANDUM" in html and "Vice President for RDIE" in html and "F-COR-001" not in html

    design = write(generator.create_draft(staff, kind=Kind.ACTIVITY_DESIGN, activity_id=draft_activity.pk),
                   fake_gemini, rationale="Because.", expected_outputs="Pitches")
    data, _ = generator.export_docx(staff, design)
    document = Docx(io.BytesIO(data))
    cells = "\n".join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
    assert "Snacks" in cells and "₱3,020.00" in cells and "Duration" in cells and "Pitch Night" in cells
    assert generator.export_pdf(staff, design)[0].startswith(b"%PDF-")
    html = renderer.rendered[-1]
    for part in ("ACTIVITY DESIGN", "I. Rationale", "II. Objectives", "Pitch to investors", "III. Expected Outputs",
                 "IV. Budget", "₱3,020.00", "Recommending Approval:"):
        assert part in html, part


def test_without_the_key_the_draft_still_has_its_facts(staff, meals, raf, monkeypatch, settings, renderer) -> None:
    from assistant import gemini

    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = ""
    version = write(letter(staff, meals, raf))
    assert version.state == ReportDraftVersion.State.READY and version.sections == {
        "body": {"text": "", "ai": False, "unverified": []}}
    assert version.facts["proposal"]["funds"] == f"₱10,500.00 – {raf}"
    assert generator.export_docx(staff, version)[0][:2] == b"PK"
    assert generator.export_pdf(staff, version)[0].startswith(b"%PDF-")


# --- Never saved to the documents --------------------------------------------------------------------------------------


def test_no_save_to_documents_for_the_drafts_only_submit(staff, director, draft_activity, fake_gemini, renderer,
                                                         client) -> None:
    version = write(generator.create_draft(staff, kind=Kind.ACTIVITY_DESIGN, activity_id=draft_activity.pk),
                    fake_gemini, rationale="Because.", expected_outputs="Pitches")
    assert generator.export_refused_reason(version) is None
    assert generator.save_refused_reason(version) == generator.DRAFT_NOT_SAVED
    with pytest.raises(generator.GeneratorError, match="not saved to the documents"):
        generator.save_to_documents(staff, version)
    client.force_login(staff)
    page = client.get(reverse("reports:generator_draft", args=[version.draft_id])).content.decode()
    assert "Save to the activity" not in page and "not saved to the documents" in page and "Submit to the Director" in page
    assert client.post(reverse("reports:generator_save", args=[version.pk])).status_code == 302

    submission = generator.submit_to_director(staff, ReportDraftVersion.objects.get(pk=version.pk))
    assert submission.activity == draft_activity and submission.document_type is None
    approval_services.approve_submission(director, submission)
    submission.refresh_from_db()
    actions = approval_services.allowed_actions(director, submission)
    assert actions["save"] is False and actions["draft_only"] is True
    with pytest.raises(approval_services.SubmissionError, match="not saved to the documents"):
        approval_services.save_to_documents(director, submission, document_type=None)
    # Nothing reached the activity's documents, and the activity is still a Draft (never auto-Scheduled, item 431).
    assert not Document.objects.filter(activity=draft_activity).exists()
    draft_activity.refresh_from_db()
    assert draft_activity.status.name == ActivityStatus.DRAFT
    detail = client.get(reverse("approvals:detail", args=[submission.pk])).content.decode()
    assert "not saved to the documents" in detail


def test_other_generated_submissions_can_still_be_saved(staff, director, workshop, fake_gemini, renderer) -> None:
    from .test_generator import GOOD

    fake_gemini.replies.append(reply(**GOOD))
    draft = generator.create_draft(staff, kind=Kind.ACTIVITY_REPORT, activity_id=workshop.pk)
    version = generator.latest_version(draft)
    generator.generate_version(version.pk, now=TEST_NOW)
    version.refresh_from_db()
    submission = generator.submit_to_director(staff, version)
    approval_services.approve_submission(director, submission)
    submission.refresh_from_db()
    actions = approval_services.allowed_actions(staff, submission)
    assert actions["save"] is True and actions["draft_only"] is False


# --- Pages ------------------------------------------------------------------------------------------------------------------


def test_creating_a_typed_draft_through_the_page(staff, meals, raf, draft_activity, client,
                                                 django_capture_on_commit_callbacks) -> None:
    client.force_login(staff)
    page = client.get(reverse("reports:list") + "?kind=request_letter").content.decode()  # the one Reports page (461)
    assert "Type the details (no activity yet)" in page and "Pitch Night" in page
    post = {"kind": "request_letter", "proposal_scope": "typed", "proposal_title": "Bootcamp",
            "proposal_start_date": "2027-02-10", "proposal_end_date": "2027-02-10", "proposal_venue": "Hub",
            "proposal_fund_source": str(raf.pk), "budget-0-category": str(meals.pk), "budget-0-particulars": "Lunch",
            "budget-0-quantity": "abc", "budget-0-unit": "pax", "budget-0-days": "1", "budget-0-unit_price": "100",
            "memo_for_name": "Dr. Rolyn Daguil"}
    refused = client.post(reverse("reports:generator"), post)
    assert refused.status_code == 400 and 'value="Bootcamp"' in refused.content.decode()  # typed values kept
    with django_capture_on_commit_callbacks():
        response = client.post(reverse("reports:generator"), {**post, "budget-0-quantity": "20"})
    draft = ReportDraft.objects.get()
    assert response.status_code == 302 and draft.activity is None and draft.options["proposal"]["title"] == "Bootcamp"
    assert draft.options["memo_for_name"] == "Dr. Rolyn Daguil"
    assert 'value="Dr. Rolyn Daguil"' in client.get(reverse("reports:list") + "?kind=request_letter").content.decode()
    with django_capture_on_commit_callbacks():
        client.post(reverse("reports:generator"), {"kind": "activity_design", "proposal_scope": "activity",
                                                   "proposal_activity": str(draft_activity.pk)})
    assert ReportDraft.objects.filter(kind=Kind.ACTIVITY_DESIGN, activity=draft_activity).exists()
    missing = client.post(reverse("reports:generator"), {"kind": "activity_design", "proposal_scope": "activity"})
    assert missing.status_code == 400 and "Choose a Draft or Scheduled activity" in missing.content.decode()
