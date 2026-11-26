"""Invented sample files for the user-testing seed (`manage.py seed_testing`, ASSUMPTIONS 464).

Pure Python (Pillow and a tiny hand-written PDF writer), so the seed needs neither WeasyPrint nor a network: the PDFs
hold real, extractable text (the AI assistant can answer questions about them), the images look like a drawn
signature, an uploaded photo of a signature, an event photo and a scanned paper attendance sheet. Everything in them is
made up.
"""

import base64
import random
import textwrap
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --- PDF ---------------------------------------------------------------------------------------------------------------

PAGE_WIDTH, PAGE_HEIGHT = 595, 842  # A4 in points
MARGIN = 56
LINE_HEIGHT = 15
WRAP = 88  # characters per line at 10.5 pt Helvetica


def _pdf_text(value: str) -> str:
    """Escape a line for a PDF string literal (Latin-1 only: other characters become "?")."""
    value = value.encode("latin-1", "replace").decode("latin-1")
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pages(title: str, paragraphs: list[str]) -> list[list[tuple[str, bool]]]:
    """Wrap the text into pages of (line, bold) pairs."""
    lines: list[tuple[str, bool]] = [(title, True), ("", False)]
    for paragraph in paragraphs:
        if paragraph.startswith("## "):
            lines += [("", False), (paragraph[3:], True)]
            continue
        for line in textwrap.wrap(paragraph, WRAP) or [""]:
            lines.append((line, False))
        lines.append(("", False))
    per_page = (PAGE_HEIGHT - 2 * MARGIN) // LINE_HEIGHT
    return [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]


def make_pdf(title: str, paragraphs: list[str]) -> bytes:
    """A small, valid PDF (Helvetica, A4) with the title in bold and the paragraphs wrapped. A paragraph that starts
    with "## " is a bold heading. pypdf (the assistant's reader) extracts the text."""
    pages = _pages(title, paragraphs)
    objects: list[bytes] = []
    # 1 catalog, 2 pages, 3 regular font, 4 bold font, then per page: page object + content stream.
    page_ids = [5 + 2 * index for index in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    for index, page in enumerate(pages):
        content = ["BT", f"1 0 0 1 {MARGIN} {PAGE_HEIGHT - MARGIN} Tm", f"{LINE_HEIGHT} TL"]
        for line, bold in page:
            content.append(f"/{'F2' if bold else 'F1'} {12 if bold else 10.5} Tf")
            content.append(f"({_pdf_text(line)}) Tj T*")
        content.append("ET")
        stream = "\n".join(content).encode("latin-1")
        content_id = page_ids[index] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>".encode())
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


# --- Images ------------------------------------------------------------------------------------------------------------


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scribble(draw: ImageDraw.ImageDraw, rng: random.Random, box: tuple[int, int, int, int], colour, width: int) -> None:
    left, top, right, bottom = box
    middle = (top + bottom) // 2
    points = []
    x = left
    while x < right:
        points.append((x, middle + rng.randint(-(bottom - top) // 3, (bottom - top) // 3)))
        x += rng.randint(12, 26)
    draw.line(points, fill=colour, width=width, joint="curve")
    draw.line([(left + 10, bottom - 8), (right - 20, bottom - 14)], fill=colour, width=max(1, width - 1))


def drawn_signature_data_url(seed: int) -> str:
    """A signature "drawn on the phone": a PNG with a transparent background, as the canvas sends it (data URL)."""
    rng = random.Random(seed)
    image = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    _scribble(ImageDraw.Draw(image), rng, (40, 40, 560, 170), (20, 30, 60, 255), 5)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def uploaded_signature_jpg(seed: int) -> bytes:
    """A photo of a signature on paper (JPG, off-white, slightly blurred)."""
    rng = random.Random(seed)
    image = Image.new("RGB", (800, 300), (238, 235, 226))
    _scribble(ImageDraw.Draw(image), rng, (60, 60, 740, 250), (25, 35, 90), 6)
    buffer = BytesIO()
    image.filter(ImageFilter.GaussianBlur(0.8)).save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def director_signature_png() -> bytes:
    """The Director's sample signature image (transparent PNG) for the certificates and the stamped submissions."""
    rng = random.Random(4646)
    image = Image.new("RGBA", (640, 220), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _scribble(draw, rng, (40, 50, 600, 190), (15, 40, 110, 255), 6)
    draw.arc((60, 60, 220, 190), 200, 340, fill=(15, 40, 110, 255), width=5)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def event_photo_jpg(seed: int, caption: str) -> bytes:
    """An invented "event photo": a room with rows of chairs, people as simple shapes and a screen (JPG)."""
    rng = random.Random(seed)
    image = Image.new("RGB", (1200, 800), (214, 205, 188))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 520, 1200, 800), fill=(150, 122, 95))  # floor
    draw.rectangle((380, 90, 820, 330), fill=(245, 245, 240), outline=(60, 60, 60), width=6)  # screen
    draw.text((410, 190), caption[:40], fill=(30, 70, 50), font=_font(28))
    for row in range(3):
        for seat in range(8):
            x, y = 90 + seat * 135 + rng.randint(-8, 8), 470 + row * 100
            colour = rng.choice([(36, 64, 120), (160, 40, 40), (40, 110, 70), (90, 90, 90), (200, 160, 40)])
            draw.ellipse((x, y - 70, x + 46, y - 24), fill=(120, 85, 60))  # head
            draw.rounded_rectangle((x - 12, y - 24, x + 58, y + 50), radius=14, fill=colour)  # body
    buffer = BytesIO()
    image.filter(ImageFilter.GaussianBlur(0.6)).save(buffer, "JPEG", quality=82)
    return buffer.getvalue()


def scanned_attendance_jpg(title: str, day_text: str, rows: list[tuple[str, str, str]]) -> bytes:
    """A scanned-looking paper ATTENDANCE sheet (JPG, A4 at ~100 dpi): grey paper, slightly rotated, printed headings
    and handwritten-looking rows (name, designation, office) with signature scribbles. For "Read the rows with AI"."""
    rng = random.Random(len(rows) * 97 + len(title))
    width, height = 1240, 1754
    image = Image.new("RGB", (width, height), (243, 241, 236))
    draw = ImageDraw.Draw(image)
    heading, small, hand = _font(34), _font(22), _font(26)
    draw.text((width // 2 - 160, 70), "ATTENDANCE", fill=(20, 20, 20), font=heading)
    draw.text((90, 140), title[:70], fill=(30, 30, 30), font=small)
    draw.text((90, 175), day_text, fill=(30, 30, 30), font=small)
    columns = [(70, "No."), (150, "Name"), (520, "Designation"), (780, "Office / Agency"), (1040, "Signature")]
    top = 240
    draw.rectangle((60, top, width - 60, top + 50), outline=(40, 40, 40), width=2)
    for x, label in columns:
        draw.text((x + 6, top + 12), label, fill=(20, 20, 20), font=small)
    for index, (name, designation, office) in enumerate(rows, start=1):
        y = top + 50 + (index - 1) * 62
        draw.rectangle((60, y, width - 60, y + 62), outline=(70, 70, 70), width=1)
        ink = (25, 40, 110)
        draw.text((80, y + 18), str(index), fill=(20, 20, 20), font=small)
        draw.text((156 + rng.randint(-3, 3), y + 16 + rng.randint(-2, 2)), name, fill=ink, font=hand)
        draw.text((526, y + 18), designation[:20], fill=ink, font=small)
        draw.text((786, y + 18), office[:20], fill=ink, font=small)
        _scribble(draw, rng, (1050, y + 10, 1170, y + 54), ink, 3)
    for x, _label in columns[1:]:
        draw.line((x, top, x, top + 50 + len(rows) * 62), fill=(70, 70, 70), width=1)
    noise = Image.effect_noise((width, height), 18).convert("RGB")
    image = Image.blend(image, noise, 0.06).rotate(0.8, expand=False, fillcolor=(236, 234, 229))
    buffer = BytesIO()
    image.filter(ImageFilter.GaussianBlur(0.7)).save(buffer, "JPEG", quality=70)
    return buffer.getvalue()
