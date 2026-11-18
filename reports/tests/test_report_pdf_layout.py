"""Nothing in a report PDF is cut off (ASSUMPTIONS 385), checked with REAL WeasyPrint. Opt-in (slow, needs Pango):
    $env:PDF_LAYOUT_TESTS = "1"; pytest -n 0 -s reports/tests/test_report_pdf_layout.py; Remove-Item Env:PDF_LAYOUT_TESTS
It fills the TEST database with the demo data (seed_demo: invented names, never the development database) plus one
activity with a very long title, venue and an unbroken word, builds every report type (its default sections) and one
report with every section, plus one activity's evaluation summary and one participant's evaluation (ASSUMPTIONS
401-402, with a very long text answer), lays each out with WeasyPrint and fails if any box on any page reaches past the page content
area, or any text past its table cell. With PDF_LAYOUT_OUT=<folder> the PDFs are also written there, and with pypdfium2
(requirements-dev.txt) the first pages as PNG images too.
"""

import os
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from reports import content as report_content
from reports.content import AI_LABEL, AI_SUMMARY, REPORT_TYPES, SECTIONS
from reports.metrics import ReportFilters, today_in_manila
from reports.pdf import individual_evaluation_html, report_html

pytestmark = [
    pytest.mark.real_weasyprint,
    pytest.mark.skipif(os.environ.get("PDF_LAYOUT_TESTS") != "1", reason="PDF layout test: set PDF_LAYOUT_TESTS=1"),
]

TOLERANCE_PX = 0.5  # rounding of the layout
PNG_PAGES = 2  # first pages written as PNG (PDF_LAYOUT_OUT only)
LONG_WORD = "Supercalifragilisticexpialidocious" * 2 + "Incubation"  # 78 characters without a space
AI_TEXT = ("This paragraph stands in for the AI summary so the layout of its box is checked too. " * 6).strip()


@pytest.fixture
def weasyprint():
    try:
        import weasyprint as module

        module.HTML(string="<p>check</p>").write_pdf()
    except (ImportError, OSError) as error:
        pytest.skip(f"WeasyPrint's system libraries are not available: {error}")
    return module


def add_long_text_activity(today) -> None:
    """A Conducted activity whose title and venue are much longer than usual, with an unbroken word in both."""
    from accounts.models import TBIMember
    from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory

    director = TBIMember.objects.get(email="director@demo.example.com")
    start = today - timedelta(days=60)
    activity = Activity.objects.create(
        created_by=director,
        status=ActivityStatus.objects.get(name=ActivityStatus.CONDUCTED),
        activity_type=ActivityType.objects.filter(is_active=True).order_by("pk").first(),
        title=f"[DEMO] Regional Technology Business Incubation Summit and Startup Investment Readiness Bootcamp {LONG_WORD}",
        start_date=start, end_date=start + timedelta(days=33),  # over two months, ended: in the period
        start_time=time(10, 0), end_time=time(12, 0),  # "10:00 AM–12:00 PM": the longest time text
        venue=f"Function Hall B, Third Floor, Hiraya Building, Caraga State University Main Campus {LONG_WORD}",
        max_slots=100,
        registration_deadline=timezone.now() - timedelta(days=30),
        evaluation_deadline=timezone.now() + timedelta(days=7),
        budget_amount=Decimal("1000.00"),
    )
    activity.participant_categories.set(ParticipantCategory.objects.all())  # every category: a long list


def overflows(page, page_number: int, name: str) -> list[str]:
    """Boxes past the right edge of the page content area, and text past the right edge of its table cell."""
    from weasyprint.formatting_structure import boxes

    page_box = page._page_box
    right = page_box.content_box_x() + page_box.width + TOLERANCE_PX
    problems = []

    def text_of(box) -> str:
        if isinstance(box, boxes.TextBox):
            return box.text
        return " ".join(text_of(child) for child in getattr(box, "children", []))[:60]

    def walk(box, cell_right=None) -> None:
        if isinstance(box, (boxes.MarginBox, boxes.TableColumnGroupBox, boxes.TableColumnBox)):
            return  # the footer lives in the margin; column boxes have no place of their own
        if isinstance(box, boxes.TableCellBox):
            cell_right = box.position_x + box.border_width() + TOLERANCE_PX
        box_right = box.position_x + box.margin_width() if not isinstance(box, boxes.TextBox) else box.position_x + box.width
        if box is not page_box and box_right > right:
            problems.append(f"{name} page {page_number}: {type(box).__name__} ends {box_right - right:.1f}px past the "
                            f"page content area: {text_of(box)!r}")
        elif cell_right is not None and isinstance(box, boxes.TextBox) and box_right > cell_right:
            problems.append(f"{name} page {page_number}: text ends {box_right - cell_right:.1f}px past its cell: "
                            f"{box.text!r}")
        for child in getattr(box, "children", []):
            walk(child, cell_right)

    walk(page_box)
    return problems


def evaluation_contents() -> dict:
    """One demo activity's evaluation summary and one participant's evaluation, with a long unbroken text answer."""
    from accounts.models import TBIMember
    from registrations.models import EvaluationSubmission, FieldType, FormAnswer, FormField
    from registrations.services import evaluation_answers
    from reports.metrics import activity_evaluation_summary

    submission = EvaluationSubmission.objects.select_related("registration__activity__activity_type").order_by("pk").first()
    assert submission, "seed_demo made no evaluations"
    field = FormField.objects.create(template=submission.template, field_label="What should we improve next time?",
                                     field_type=FieldType.LONG_TEXT, sort_order=99)
    FormAnswer.objects.create(field=field, answer_source=FormAnswer.AnswerSource.EVALUATION, evaluation_submission=submission,
                              answer_value=("More hands-on exercises and a longer pitching session. " * 8) + LONG_WORD)
    activity, director = submission.registration.activity, TBIMember.objects.get(email="director@demo.example.com")
    now = timezone.now()
    return {
        "evaluation-summary": report_content.build_evaluation_summary(
            activity, activity_evaluation_summary(activity), generated_by="Demo Director", generated_at=now),
        # Its own portrait template (ASSUMPTIONS 404): passed as finished HTML.
        "individual-evaluation": individual_evaluation_html(
            activity, evaluation_answers(director, submission.registration), generated_by="Demo Director", generated_at=now),
    }


def write_pngs(pdf_path: Path, pages: int) -> None:
    try:
        import pypdfium2
    except ImportError:
        print("pypdfium2 is not installed (pip install -r requirements-dev.txt): no PNG images.")
        return
    document = pypdfium2.PdfDocument(str(pdf_path))
    try:
        for index in range(min(pages, len(document))):
            image = document[index].render(scale=150 / 72).to_pil()  # 150 dpi
            image.save(pdf_path.with_name(f"{pdf_path.stem}-page{index + 1}.png"))
    finally:
        document.close()


@pytest.mark.django_db
def test_no_report_pdf_is_cut_off(weasyprint, settings, tmp_path) -> None:
    settings.DEBUG = True  # seed_demo is demo-only
    settings.MEDIA_ROOT = tmp_path
    call_command("seed_demo", verbosity=0)
    today = today_in_manila()
    add_long_text_activity(today)
    filters = ReportFilters(today - timedelta(days=365), today)

    reports = {key: list(report.default_sections) for key, report in REPORT_TYPES.items()}
    reports["all-sections"] = list(SECTIONS)
    out = Path(os.environ["PDF_LAYOUT_OUT"]) if os.environ.get("PDF_LAYOUT_OUT") else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    fetcher = weasyprint.URLFetcher(allowed_protocols=["data"])  # as certificates.pdf.WeasyPrintRenderer

    contents = {}
    for name, sections in reports.items():
        report_type = name if name in REPORT_TYPES else "accomplishment"
        content = report_content.build_content(
            report_type, sections, filters, generated_by="Demo Director", generated_at=timezone.now(),
        )
        if ai := content.section(AI_SUMMARY):
            ai.note, ai.text = AI_LABEL, AI_TEXT
        assert content.has_activities, "seed_demo made no conducted activities in the last year"
        if activities := content.section("activities"):
            assert any(LONG_WORD in row[0] for row in activities.tables[0].rows), "the long-text activity is missing"
        contents[name] = content
    contents.update(evaluation_contents())

    problems = []
    for name, content in contents.items():
        html = content if isinstance(content, str) else report_html(content)
        document = weasyprint.HTML(string=html, url_fetcher=fetcher).render()
        found = [problem for number, page in enumerate(document.pages, start=1)
                 for problem in overflows(page, number, name)]
        print(f"{name:22} {len(document.pages)} pages, {len(found)} problems")
        problems += found
        if out:
            pdf_path = out / f"report-{name}.pdf"
            document.write_pdf(pdf_path)
            write_pngs(pdf_path, PNG_PAGES)
    assert not problems, "\n".join(problems[:40])
