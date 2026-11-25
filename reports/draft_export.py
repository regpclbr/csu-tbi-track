"""Exporting a report draft version (ASSUMPTIONS 442-445): PDF (WeasyPrint through certificates.pdf.get_renderer(),
A4 portrait, template reports/draft_pdf.html) and DOCX (python-docx, editable). Layout only: every number, date, name
list and amount comes from the version's facts (reports.draft_facts), the narrative from its sections. The "AI-generated
draft" label is shown only in the editor, never in an export.

The Activity Report's PDF annex appends the activity's newest Attendance Sheet and Confirmation of Attendance documents
(PDFs; a JPG / PNG scan becomes an image page); when there is none, the generated form (item 434) is rendered in memory
(never saved). The DOCX annex has the same name lists as tables (no signature images). Photos: the version's photo list
(Event Photos documents, the draft's photos, participants' photos ticked "Use in reports"), shrunk by Pillow, 6 a page.
"""

import base64
import io
import logging
import re

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.safestring import mark_safe

from documents.models import Document
from registrations import attendance_forms

from .content import Chart
from .generator import is_list_section, photo_bytes, section_specs
from .models import ReportDraft, ReportDraftVersion
from .pdf import bar_chart_svg

logger = logging.getLogger("reports")

KIND_HEADINGS = {ReportDraft.Kind.ACTIVITY_REPORT: "ACTIVITY REPORT",
                 ReportDraft.Kind.EVALUATION_SUMMARY: "EVALUATION SUMMARY REPORT",
                 ReportDraft.Kind.REQUEST_LETTER: "MEMORANDUM",
                 ReportDraft.Kind.ACTIVITY_DESIGN: "ACTIVITY DESIGN",
                 ReportDraft.Kind.OTHER: "REPORT"}
BLANK_LINE = "______________________________"


def heading(version: ReportDraftVersion) -> str:
    """The big heading: the Accomplishment Report's comes from its period ("3RD QUARTER ACCOMPLISHMENT REPORT")."""
    if version.draft.kind == ReportDraft.Kind.ACCOMPLISHMENT_REPORT:
        return (version.facts.get("header") or {}).get("heading", "ACCOMPLISHMENT REPORT")
    return KIND_HEADINGS[version.draft.kind]


ACCOMPLISHMENT_COUNTS = [("activities_conducted", "Activities conducted"), ("registered", "Registered (cancelled not counted)"),
                         ("cancelled", "Cancelled (“Will not attend”)"), ("attended", "Attended"),
                         ("evaluations", "Evaluations submitted"), ("response_rate", "Response rate (of attendees in the system)"),
                         ("average_rating", "Average rating (1-5)"), ("budget_total", "Total budget")]
DESIGN_ROWS = [("title", "Title"), ("duration", "Duration"), ("dates", "Date"), ("time", "Time"), ("venue", "Venue"),
               ("participants", "Participants"), ("funds", "Funds")]


def signature_blocks(facts: dict) -> list[dict]:
    """The signature blocks at the end: the draft letter / design's own (facts["signatures"]), else Prepared by (the
    member + POSITION) and Noted (the TBI Director's linked signatory; a blank line when none)."""
    if facts.get("signatures"):
        return facts["signatures"]
    prepared, noted = facts.get("prepared_by", {}), facts.get("noted_by", {})
    return [{"label": "Prepared by:", "name": prepared.get("name", ""), "position": prepared.get("position", "")},
            {"label": "Noted:", "name": noted.get("name", ""), "position": noted.get("position", "")}]


def accomplishment_rows(version: ReportDraftVersion, blocks: dict) -> list[dict]:
    """Table B (Accomplishments vis-à-vis Objectives): per activity its objectives (ACTIVITY.objectives, item 428, else
    the section the AI read from its Activity Design) and the accomplishments section."""
    rows = []
    for item in version.facts.get("activities", []):
        key = f"activity_{item['activity_id']}"
        objectives = item["objectives"] or (blocks.get(f"{key}_objectives") or {}).get("items", [])
        rows.append({"item": item, "objectives": objectives,
                     "objectives_read": not item["objectives"] and bool(objectives),
                     "accomplishments": (blocks.get(f"{key}_accomplishments") or {}).get("items", []),
                     "highlights": blocks.get(f"{key}_highlights") or {"label": item["title"], "paragraphs": []}})
    return rows
BULLET = re.compile(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+")


class NotAPdf(Exception):
    """A part of the PDF could not be read (the renderer or an annex file)."""


def paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]


def items(text: str) -> list[str]:
    return [BULLET.sub("", line).strip() for line in (text or "").splitlines() if BULLET.sub("", line).strip()]


def section_blocks(version: ReportDraftVersion) -> dict:
    """{key: {"label", "paragraphs" | "items"}} from the version's sections (their filled-in text)."""
    blocks = {}
    for key, label, _ in section_specs(version.draft.kind, version.facts):
        text = (version.sections.get(key) or {}).get("text", "")
        listed = is_list_section(key)
        blocks[key] = {"label": label, "text": text, "items": items(text) if listed else [],
                       "paragraphs": [] if listed else paragraphs(text)}
    return blocks


def _shrunk(data: bytes) -> bytes | None:
    """A photo as a JPEG at most REPORT_GENERATOR_PHOTO_MAX_SIDE, upright, no metadata (None when it cannot be read)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
                image = background
            image.thumbnail((settings.REPORT_GENERATOR_PHOTO_MAX_SIDE,) * 2)
            out = io.BytesIO()
            image.convert("RGB").save(out, "JPEG", quality=82)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def photos(version: ReportDraftVersion) -> list[dict]:
    """The version's photos as JPEG bytes with their captions (gone or unreadable ones are left out)."""
    result = []
    for photo in version.facts.get("photos", []):
        found = photo_bytes(photo)
        jpeg = _shrunk(found[0]) if found else None
        if jpeg:
            result.append({"jpeg": jpeg, "caption": photo.get("caption", "")})
    return result


def _pages(items_: list, size: int) -> list[list]:
    return [items_[start:start + size] for start in range(0, len(items_), size)]


def _mix_chart(mix: dict) -> str:
    if not mix or not mix.get("total"):
        return ""
    return mark_safe(bar_chart_svg(Chart(f"Participant mix ({mix['basis']})", [(row["group"], row["count"]) for row in mix["rows"]])))


def _image_annex(activity_id: int | None, document_id: int | None) -> dict | None:
    """A JPG / PNG annex document as an image page (a PDF one is appended instead)."""
    if not document_id:
        return None
    document = Document.objects.filter(pk=document_id, activity_id=activity_id).first()
    if document is None or document.file_extension.lower() not in ("jpg", "png"):
        return None
    try:
        with document.file_path.open("rb") as handle:
            jpeg = _shrunk(handle.read())
    except (FileNotFoundError, ValueError, OSError):
        return None
    return {"title": document.title, "uri": f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}"} if jpeg else None


def count_label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def pdf_html(version: ReportDraftVersion, annex_follows: bool = False) -> str:
    draft, facts = version.draft, version.facts
    shown = [{"uri": f"data:image/jpeg;base64,{base64.b64encode(p['jpeg']).decode()}", "caption": p["caption"]}
             for p in photos(version)]
    annex = facts.get("annex") or {}
    blocks = section_blocks(version)
    context = {
        "kind": draft.kind, "heading": heading(version), "draft": draft, "facts": facts,
        "header": facts.get("header", {}), "letterhead": attendance_forms.PLACEHOLDER_HEADER,
        "sections": blocks, "activity_rows": accomplishment_rows(version, blocks) if facts.get("activities") and
        draft.kind == ReportDraft.Kind.ACCOMPLISHMENT_REPORT else [],
        "objectives": facts.get("proposal", {}).get("objectives") or blocks.get("objectives", {}).get("items", []),
        "photo_pages": _pages(shown, settings.REPORT_GENERATOR_PHOTOS_PER_PAGE),
        "mix_chart": _mix_chart(facts.get("mix")),
        "image_annex": [item for item in (_image_annex(draft.activity_id, annex.get("attendance_document_id")),
                                          _image_annex(draft.activity_id, annex.get("confirmation_document_id"))) if item],
        "generated_at": timezone.localtime(),
        "form_code": facts.get("form_code", ""),
        "count_rows": [(count_label(key), value) for key, value in (facts.get("counts") or {}).items()],
        "annex_follows": annex_follows,
        "signatures": signature_blocks(facts),
        "memo_parts": [(label, (facts.get("memo") or {}).get(key, {})) for label, key in
                       (("FOR:", "for"), ("THRU:", "thru"), ("FROM:", "from"))],
        "accomplishment_counts": [(label, (facts.get("counts") or {}).get(key, "")) for key, label in ACCOMPLISHMENT_COUNTS],
        "design_rows": [(label, (facts.get("proposal") or {}).get(key, "")) for key, label in DESIGN_ROWS],
    }
    return render_to_string("reports/draft_pdf.html", context)


def _annex_pdfs(version: ReportDraftVersion) -> list[bytes]:
    """The Activity Report's appended forms: the newest Attendance Sheet / Confirmation of Attendance PDF documents, or
    the generated form rendered in memory when the activity has none (and it can be generated)."""
    from certificates.pdf import get_renderer

    draft, annex = version.draft, version.facts.get("annex") or {}
    if draft.kind != ReportDraft.Kind.ACTIVITY_REPORT or draft.activity_id is None:
        return []
    parts = []
    for kind, key in ((attendance_forms.ATTENDANCE, "attendance_document_id"),
                      (attendance_forms.CONFIRMATION, "confirmation_document_id")):
        document = Document.objects.filter(pk=annex.get(key), activity_id=draft.activity_id).first() if annex.get(key) else None
        if document is not None:
            if document.file_extension.lower() == "pdf":
                try:
                    with document.file_path.open("rb") as handle:
                        parts.append(handle.read())
                except (FileNotFoundError, ValueError, OSError):
                    logger.warning("Report draft %s: annex document %s is missing on disk.", draft.pk, document.pk)
            continue  # an image scan is an image page in the HTML annex
        if attendance_forms.refused_reason(draft.activity, kind) is None:
            parts.append(get_renderer().render(
                attendance_forms.form_html(draft.activity, kind, version.created_by)))
    return parts


def merge_pdfs(parts: list[bytes]) -> bytes:
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError

    if len(parts) == 1:
        return parts[0]
    writer = PdfWriter()
    for part in parts:
        try:
            writer.append(PdfReader(io.BytesIO(part)))
        except (PdfReadError, ValueError, OSError) as error:
            raise NotAPdf("A part of the report could not be read as a PDF.") from error
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def render_pdf(version: ReportDraftVersion) -> bytes:
    from certificates.pdf import get_renderer

    annex = _annex_pdfs(version)
    main = get_renderer().render(pdf_html(version, annex_follows=bool(annex)))
    if not main.startswith(b"%PDF-"):
        raise NotAPdf("The PDF renderer returned something that is not a PDF.")
    return merge_pdfs([main, *annex])


# --- DOCX ---------------------------------------------------------------------------------------------------------------


def render_docx(version: ReportDraftVersion) -> bytes:
    """An editable Word file with the same content (no "AI-generated" label, no signature images)."""
    from docx import Document as Docx
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.shared import Cm, Pt

    draft, facts = version.draft, version.facts
    header, blocks = facts.get("header", {}), section_blocks(version)
    doc = Docx()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for side in ("left_margin", "right_margin"):
        setattr(section, side, Cm(2.0))
    section.top_margin, section.bottom_margin = Cm(1.8), Cm(1.8)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = "Arial", Pt(10.5)
    if facts.get("form_code"):
        section.footer.paragraphs[0].text = facts["form_code"]
        section.footer.paragraphs[0].runs[0].font.size = Pt(8)

    def centered(text: str, *, bold: bool = False, size: float | None = None) -> None:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(text)
        run.bold = bold
        if size:
            run.font.size = Pt(size)

    def heading_(text: str, level: int = 2) -> None:
        doc.add_heading(text, level=level)

    def table(columns: list[str], rows: list[list], widths: list[float] | None = None) -> None:
        grid = doc.add_table(rows=1, cols=len(columns))
        grid.style = "Table Grid"
        grid.alignment = WD_TABLE_ALIGNMENT.CENTER
        for cell, label in zip(grid.rows[0].cells, columns):
            cell.text = label
            cell.paragraphs[0].runs[0].bold = True
        for row in rows:
            cells = grid.add_row().cells
            for cell, value in zip(cells, row):
                cell.text = "" if value is None else str(value)
        if widths:
            for row in grid.rows:
                for cell, width in zip(row.cells, widths):
                    cell.width = Cm(width)

    def text_block(key: str) -> None:
        block = blocks.get(key)
        if not block:
            return
        for paragraph in block["paragraphs"]:
            doc.add_paragraph(paragraph)
        for item in block["items"]:
            doc.add_paragraph(item, style="List Bullet")

    for line in attendance_forms.PLACEHOLDER_HEADER:
        centered(line, bold=True)
    if header.get("office_line"):
        centered(header["office_line"])
    centered(heading(version), bold=True, size=14)
    if draft.kind == ReportDraft.Kind.ACCOMPLISHMENT_REPORT:
        centered(header.get("program_name", ""), bold=True, size=12)
        centered(f"Period Covered: {header.get('period_covered', '')}")
    elif draft.kind not in (ReportDraft.Kind.REQUEST_LETTER, ReportDraft.Kind.ACTIVITY_DESIGN):
        centered(header.get("title") or draft.title, bold=True, size=12)
        meta = [("Period Covered", header.get("period_covered", "")), ("Venue", header.get("venue", "")),
                ("Implementing Unit", header.get("implementing_unit", "")),
                ("Project Leader", header.get("project_leader", ""))]
        table(["", ""], [[label, value] for label, value in meta if value], [4.5, 12.5])

    def budget_table(budget: dict) -> None:
        table(["Particulars", "Quantity", "Unit Cost", "Total Amount"],
              [[r["particulars"], r["quantity"], r["unit_cost"], r["total"]] for r in budget.get("rows", [])]
              + [["Total", "", "", budget.get("total", "")]], [7.5, 3.5, 3, 3])

    if draft.kind == ReportDraft.Kind.ACCOMPLISHMENT_REPORT:
        counts = facts.get("counts", {})
        heading_(blocks["introduction"]["label"])
        text_block("introduction")
        heading_("Figures for the period (from the system)", 3)
        table(["Measure", "Value"], [[label, counts.get(key, "")] for key, label in ACCOMPLISHMENT_COUNTS])
        if facts.get("attendance_note"):
            doc.add_paragraph(facts["attendance_note"])
        mix = facts.get("mix") or {}
        table(["Participant mix", (mix.get("basis") or "count").capitalize(), "Share"],
              [[r["group"], r["count"], r["share"]] for r in mix.get("rows", [])])
        doc.add_paragraph(mix.get("guideline", ""))
        if facts.get("ratings"):
            table(["Evaluation question", "Answers", "Mean"], [[q["question"], q["answers"], q["mean"]] for q in facts["ratings"]])
        table(["Fund source", "Activities", "Budget"],
              [[r["fund_source"], r["activities"], r["total"]] for r in facts.get("budget_by_fund_source", [])]
              + [["Total", counts.get("activities_conducted", ""), counts.get("budget_total", "")]])
        rows = accomplishment_rows(version, blocks)
        for row in rows:
            item = row["item"]
            heading_(row["highlights"]["label"], 3)
            doc.add_paragraph(f"Date: {item['dates']} · Venue: {item['venue']} · Participants: {item['categories']} · "
                              f"Registered: {item['registered']} · Attended: {item['attended']}")
            for line in item["days"]:
                doc.add_paragraph(line, style="List Bullet")
            text_block(f"activity_{item['activity_id']}_highlights")
        if not rows:
            doc.add_paragraph("No conducted activities in this period.")
        heading_("B. Accomplishments vis-à-vis Objectives")
        table(["Objectives", "Accomplishments"],
              [[f"{row['item']['title']}\n" + "\n".join(f"• {o}" for o in row["objectives"]),
                "\n".join(f"• {a}" for a in row["accomplishments"])] for row in rows], [8.5, 8.5])
        for key in ("outputs", "problems", "recommendations"):
            heading_(blocks[key]["label"])
            if key == "problems":
                text_block(key)
            else:
                for item in blocks[key]["items"]:
                    doc.add_paragraph(item, style="List Number")
    elif draft.kind == ReportDraft.Kind.REQUEST_LETTER:
        memo = facts.get("memo", {})
        table(["", ""], [["FOR:", f"{(memo['for']['name'] or BLANK_LINE).upper()}\n{memo['for']['title']}"],
                         ["THRU:", f"{(memo['thru']['name'] or BLANK_LINE).upper()}\n{memo['thru']['title']}"],
                         ["FROM:", f"{(memo['from']['name'] or BLANK_LINE).upper()}\n{memo['from']['title']}"],
                         ["SUBJECT:", memo["subject"]], ["DATE:", memo["date"]]], [3, 14])
        text_block("body")
    elif draft.kind == ReportDraft.Kind.ACTIVITY_DESIGN:
        proposal = facts.get("proposal", {})
        centered(proposal.get("title", ""), bold=True, size=12)
        table(["", ""], [[label, proposal.get(key, "")] for key, label in DESIGN_ROWS], [4, 13])
        heading_("I. Rationale")
        text_block("rationale")
        heading_("II. Objectives")
        for line in proposal.get("objectives") or blocks.get("objectives", {}).get("items", []):
            doc.add_paragraph(line, style="List Number")
        heading_("III. Expected Outputs")
        for line in blocks["expected_outputs"]["items"]:
            doc.add_paragraph(line, style="List Number")
        heading_("IV. Budget")
        budget = facts.get("budget", {})
        budget_table(budget)
        doc.add_paragraph(f"Fund source: {budget.get('fund_source', '')}.")
    elif draft.kind == ReportDraft.Kind.ACTIVITY_REPORT:
        heading_(blocks["highlights"]["label"])
        text_block("highlights")
        heading_(blocks["accomplishments"]["label"], 3)
        text_block("accomplishments")
        heading_(blocks["problems"]["label"])
        text_block("problems")
        heading_("C. Running Budget")
        budget = facts.get("budget", {})
        budget_table(budget)
        doc.add_paragraph(f"Fund source: {budget.get('fund_source', '')}. {budget.get('note', '')}")
        heading_(blocks["recommendations"]["label"])
        text_block("recommendations")
    elif draft.kind == ReportDraft.Kind.EVALUATION_SUMMARY:
        counts = facts.get("counts", {})
        heading_("Responses")
        table(["Measure", "Value"], [[count_label(key), value] for key, value in counts.items()])
        heading_("Ratings (1 lowest – 5 highest)")
        table(["Question", "Answers", "Mean", "5", "4", "3", "2", "1"],
              [[q["question"], q["answers"], q["mean"], *[f"{f['count']} ({f['share']})" for f in q["frequency"]]]
               for q in facts.get("ratings", [])])
        if facts.get("activities"):
            heading_("Activities")
            table(["Activity", "Dates", "Attended", "Evaluations", "Response rate", "Average"],
                  [[a["title"], a["dates"], a["attended"], a["evaluations"], a["response_rate"], a["average_rating"]]
                   for a in facts["activities"]])
        for key in ("overview", "open_answers", "recommendations"):
            heading_(blocks[key]["label"])
            text_block(key)
    else:
        heading_(blocks["body"]["label"])
        text_block("body")
        if facts.get("counts"):
            table(["Measure", "Value"], [[count_label(key), value] for key, value in facts["counts"].items()])

    doc.add_paragraph()
    blocks_ = signature_blocks(facts)
    signatures = doc.add_table(rows=4, cols=len(blocks_))
    for column, block in enumerate(blocks_):
        signatures.rows[0].cells[column].text = block["label"]
        signatures.rows[2].cells[column].text = (block["name"] or BLANK_LINE).upper()
        signatures.rows[3].cells[column].text = block["position"]

    shown = photos(version)
    for page in _pages(shown, settings.REPORT_GENERATOR_PHOTOS_PER_PAGE):
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        heading_("Photo Documentation")
        grid = doc.add_table(rows=(len(page) + 1) // 2, cols=2)
        for index, photo in enumerate(page):
            cell = grid.rows[index // 2].cells[index % 2]
            cell.paragraphs[0].add_run().add_picture(io.BytesIO(photo["jpeg"]), width=Cm(8))
            if photo["caption"]:
                cell.add_paragraph(photo["caption"])

    if draft.kind == ReportDraft.Kind.ACTIVITY_REPORT:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        heading_("Annex", 1)
        mix = facts.get("mix") or {}
        heading_("Participant mix")
        table(["Group", "Count", "Share"], [[r["group"], r["count"], r["share"]] for r in mix.get("rows", [])])
        doc.add_paragraph(mix.get("guideline", ""))
        for sheet in facts.get("paper_sheets", []):
            heading_(f"From the paper sheet: {sheet['kind']}{' – ' + sheet['day'] if sheet['day'] else ''}", 3)
            table(["No.", "Name", "Designation", "Office", "Category"],
                  [[r["number"], r["name"], r["designation"], r["office"], r["category"]] for r in sheet["rows"]])
            table(["Group", "Count", "Share"], [[r["group"], r["count"], r["share"]] for r in sheet["mix"]["rows"]])
        annex = facts.get("annex") or {}
        for page in annex.get("attendance_pages", []):
            heading_(f"Attendance – {page['date']}", 3)
            table(["No.", "Name", "Designation", "Office", "Time in"],
                  [[r["number"], r["name"] + (" (walk-in)" if r["walk_in"] else ""), r["designation"], r["office"], r["time"]]
                   for r in page["rows"]])
        if annex.get("confirmation_rows"):
            heading_("Confirmation of Attendance", 3)
            table(["No.", "Name", "Designation", "Answer"],
                  [[r["number"], r["name"], r["designation"], "Will attend" if r["will_attend"] else "Will not attend"]
                   for r in annex["confirmation_rows"]])
            doc.add_paragraph("The signed forms are in the PDF version of this report.")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
