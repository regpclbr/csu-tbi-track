"""seed_defense (ASSUMPTIONS 470): the safety rules, the invented people and the office documents on the letterhead.
The full seed is never run here: it EMPTIES the connected database (run it by hand on a test database)."""

from datetime import date, time
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from accounts import defense_documents as docs
from accounts.management.commands import seed_defense
from accounts.management.commands.seed_defense import ACCOUNTS, CONDUCTED, DOMAIN, Command

# Real CSU / NAVIGATU names seen in the office's sample documents: never in the mock data.
REAL_NAMES = ["Encarnacion", "Daguil", "Dellosa", "Jayoma", "Laplana", "Correa", "Barber", "Asis", "Abrea", "Ganoy",
              "Paclibar", "Pitogo", "Viste", "Tidalgo", "Clarin"]


def test_refuses_without_the_flag() -> None:
    with pytest.raises(CommandError, match="--yes-this-is-a-test-database"):
        call_command("seed_defense")


@pytest.mark.parametrize("name", ["csu_tbi_track", "CSU_TBI_TRACK", "production", "csu_tbi_uat", ""])
def test_refuses_a_database_without_test_or_demo_in_its_name(name) -> None:
    assert "must contain" in Command.refused_reason(name, confirmed=True)


@pytest.mark.parametrize("name", ["csu_tbi_testing", "csu_tbi_demo", "DEMO_tbi"])
def test_allows_a_test_or_demo_database_with_the_flag(name) -> None:
    assert Command.refused_reason(name, confirmed=True) is None
    assert Command.refused_reason(name, confirmed=False) is not None


def test_no_real_officials_names_and_only_demo_addresses() -> None:
    source = Path(seed_defense.__file__).read_text(encoding="utf-8").split("REAL_NAMES", 1)[0]
    template = (Path(settings.BASE_DIR) / "templates/defense/document_pdf.html").read_text(encoding="utf-8")
    documents_source = Path(docs.__file__).read_text(encoding="utf-8")
    for name in REAL_NAMES:
        assert name not in source and name not in template and name not in documents_source, name
    assert DOMAIN == "demo.example.com" and all(local for *_rest, local in ACCOUNTS)
    assert "@example.com\"" not in source


def test_a_year_in_every_quarter_and_narrative_text_has_no_numbers() -> None:
    """Every quarter has Conducted activities; the stand-in narrative (titles, topics, descriptions) never contains a
    digit, so the report generator's numbers rule never flags it."""
    assert {(spec.month - 1) // 3 + 1 for spec in CONDUCTED} == {1, 2, 3, 4}
    for spec in [*CONDUCTED, seed_defense.RECENT]:
        text = " ".join([spec.title, spec.description, *spec.objectives])
        assert not any(character.isdigit() for character in text), spec.title
    for person in seed_defense.RESOURCE_PERSONS:
        assert not any(character.isdigit() for character in " ".join(person)), person[0]


def details() -> docs.Details:
    return docs.Details(
        title="Mentoring Clinic: Business Model Canvas", tbi="NAVIGATU", type_name="Mentoring",
        start=date(2026, 3, 5), end=date(2026, 3, 6), start_time=time(8, 0), end_time=time(17, 0),
        venue="NAVIGATU Co-working Space", categories=["Incubatees", "MSMEs"],
        objectives=["Complete the canvas", "Find the riskiest assumptions", "Plan interviews"],
        description="An afternoon of mentoring.", fund_source="TRF – Trust Fund", fund_account="NAVIGATU – Training",
        budget=[("Meals/Snacks", "Lunch", 60, "pax", 1, "250.00"), ("Honorarium", "Mentor", 1, "person", 2, "3000.00")],
        max_slots=25, prepared_by=docs.Person("Engr. Paolo R. Villanueva", "Head, Incubation Program"),
        director=docs.Person("Dr. Elena S. Marquez", "Director, Technology Business Incubator"),
        vice_president=seed_defense.VICE_PRESIDENT, president=seed_defense.PRESIDENT,
        resource_persons=[docs.ResourcePerson("Dr. Liza M. Cabrera", "Business model canvas", "Associate Professor",
                                              "Northern Mindanao Business School", "Dr. Cabrera")])


def rendered(fake_pdf_renderer) -> str:
    return fake_pdf_renderer.rendered[-1]


def test_activity_design_on_the_letterhead_with_budget_and_signatures(fake_pdf_renderer) -> None:
    pdf = docs.activity_design(details())
    html = rendered(fake_pdf_renderer)
    assert pdf.startswith(b"%PDF-")
    assert 'class="lh"' in html and "SAMPLE – FOR SYSTEM DEMONSTRATION" in html  # also with the setting off
    assert "ACTIVITY DESIGN" in html and "Program of Activities" in html
    assert "₱15,000.00" in html and "₱6,000.00" in html and "₱21,000.00" in html  # 60 × 250, 1 × 2 days × 3,000
    for label in ("Prepared by:", "Recommending Approval:", "Approved by:"):
        assert label in html
    assert "Lunch Break" in html and "Closing Program" in html


def test_request_letter_has_the_tracking_number_and_its_qr_code(fake_pdf_renderer) -> None:
    docs.request_letter(details(), docs.tracking_number(2026, 132), date(2026, 2, 3))
    html = rendered(fake_pdf_renderer)
    assert "TBI-2026-00132" in html and 'alt="QR code of TBI-2026-00132"' in html and "data:image/png;base64," in html
    assert "FOR:" in html and "THRU:" in html and "FROM:" in html and "Incubatees and MSMEs" in html


def test_sample_line_is_printed_even_when_the_setting_is_off(fake_pdf_renderer, settings) -> None:
    settings.LETTERHEAD_SAMPLE_LINE = False
    docs.invitation_letter(details(), details().resource_persons[0], "TBI-2026-00133", date(2026, 2, 5))
    html = rendered(fake_pdf_renderer)
    assert "SAMPLE – FOR SYSTEM DEMONSTRATION" in html and "Dear Dr. Cabrera:" in html


def test_forms_and_program_documents(fake_pdf_renderer) -> None:
    docs.pre_registration_form(details(), ["Startup name"])
    assert "PRE-REGISTRATION FORM" in rendered(fake_pdf_renderer) and "Startup name" in rendered(fake_pdf_renderer)
    docs.evaluation_form(details(), ["Overall rating"], ["Comments"])
    assert "ACTIVITY EVALUATION FORM" in rendered(fake_pdf_renderer)
    docs.program_of_activities(details())
    assert "PROGRAM OF ACTIVITIES" in rendered(fake_pdf_renderer) and "Day 2 | March 6, 2026" in rendered(fake_pdf_renderer)
    docs.memo_document("Monthly Activity Summary", ["Summary."], details().prepared_by, details().director)
    assert "MONTHLY ACTIVITY SUMMARY" in rendered(fake_pdf_renderer)


def test_documents_escape_their_text(fake_pdf_renderer) -> None:
    unsafe = details()
    unsafe.title = "<script>alert(1)</script>"
    docs.activity_design(unsafe)
    assert "<script>alert" not in rendered(fake_pdf_renderer)


def test_narrative_stand_in_writes_only_known_sections() -> None:
    command = Command()
    command.narrative_kind, command.narrative_spec, command.specs_by_activity = "activity_report", CONDUCTED[0], {}
    reply = seed_defense.NarrativeGateway(command).generate('SECTIONS:\n- "highlights" (A. Highlights): x\n'
                                                            '- "problems" (B. Problems): y')
    import json

    sections = json.loads(reply.text)["sections"]
    assert set(sections) == {"highlights", "problems"} and "[[attended]]" in sections["highlights"]
    assert not any(character.isdigit() for character in sections["highlights"].replace("[[", "").replace("]]", ""))
