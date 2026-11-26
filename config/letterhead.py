"""The Caraga State University letterhead on every office-style PDF (ASSUMPTIONS 469).

One shared header for the report generator's documents, the generated attendance forms, one participant's evaluation,
the figures reports (compact) and the seed_defense documents: templates `pdf/_letterhead.html` (the header, repeated on
every page as a WeasyPrint running element) and `pdf/_letterhead_styles.html` (its CSS and the page footer).

- The seal and the "Caraga State University" wordmark are CUT from `LETTERHEAD_DIR/csu-logo.png` (found by the empty
  column between them); the pixels are never redrawn. Without the file the header falls back to text.
- The Bagong Pilipinas / SOCOTEC ISO 9001 / PAB marks appear only when their official files exist in the folder (`MARKS`);
  they are never drawn or imitated.
- Footer: the form code (left), "SAMPLE – FOR SYSTEM DEMONSTRATION" (centre, `LETTERHEAD_SAMPLE_LINE`, ON until NAVIGATU
  approves the use of the seal) and "Page X of Y" (right).
- Images go in as data: URIs only (the PDF renderer fetches nothing else).
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.utils.safestring import SafeString, mark_safe

LOGO_FILE = "csu-logo.png"
# (file name, alt text): the official marks, shown only when NAVIGATU gives the files (open question, CLAUDE.md).
MARKS = [("bagong-pilipinas.png", "Bagong Pilipinas"), ("socotec-iso9001.png", "SOCOTEC ISO 9001"), ("pab.png", "PAB")]
REPUBLIC = "Republic of the Philippines"
UNIVERSITY = "Caraga State University"
MOTTO = ("Competence", "Service", "Uprightness")
SEAL_HEIGHT_PX = 320  # the seal and wordmark are shrunk to this before they go into a PDF (small files, sharp in print)
WORDMARK_WIDTH_PX = 1600
MARK_HEIGHT_PX = 240
INK_LEVEL = 235  # a pixel darker than this (on white) is part of the logo


@dataclass(frozen=True)
class LogoParts:
    seal: str  # data: URI
    wordmark: str  # data: URI ("" when the logo could not be split: then `seal` is the whole logo)


def _png_uri(image) -> str:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return f"data:image/png;base64,{base64.b64encode(out.getvalue()).decode('ascii')}"


def _ink_mask(image):
    """A 1-bit-like mask (255 = logo) of an image flattened on white: works for transparent and white backgrounds."""
    from PIL import Image

    rgba = image.convert("RGBA")
    flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    flat.alpha_composite(rgba)
    return flat.convert("L").point(lambda value: 255 if value < INK_LEVEL else 0), flat.convert("RGB"), rgba


def split_column(mask) -> int | None:
    """The x of the empty column between the seal (left, tall) and the wordmark (right, one line of text): the first
    empty column after the first ink such that everything to its right stays inside the wordmark's band of rows (the
    rows with ink in the right half of the logo). None when there is no such column (the logo is then kept whole)."""
    width, height = mask.size
    band = mask.crop((width // 2, 0, width, height)).getbbox()
    if band is None:
        return None
    top, bottom, tolerance = band[1], band[3], max(2, (band[3] - band[1]) // 20)
    seen_ink = False
    for x in range(width):
        if mask.crop((x, 0, x + 1, height)).getbbox() is not None:
            seen_ink = True
            continue
        if not seen_ink:
            continue
        rest = mask.crop((x, 0, width, height)).getbbox()
        if rest is None:
            return None
        if rest[1] >= top - tolerance and rest[3] <= bottom + tolerance:
            return x
    return None


@lru_cache(maxsize=4)
def _logo_parts(path: str, modified: float) -> LogoParts | None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            mask, _flat, rgba = _ink_mask(image)
    except (OSError, UnidentifiedImageError, ValueError):
        return None
    split = split_column(mask)
    if split is None:
        whole = rgba.crop(mask.getbbox() or (0, 0, *rgba.size))
        whole.thumbnail((WORDMARK_WIDTH_PX, SEAL_HEIGHT_PX))
        return LogoParts(seal=_png_uri(whole), wordmark="")
    width, height = rgba.size
    seal_box = mask.crop((0, 0, split, height)).getbbox()
    word_box = mask.crop((split, 0, width, height)).getbbox()
    if not seal_box or not word_box:
        return None
    seal = rgba.crop(seal_box)
    wordmark = rgba.crop((split + word_box[0], word_box[1], split + word_box[2], word_box[3]))
    seal.thumbnail((SEAL_HEIGHT_PX * 2, SEAL_HEIGHT_PX))
    wordmark.thumbnail((WORDMARK_WIDTH_PX, WORDMARK_WIDTH_PX))
    return LogoParts(seal=_png_uri(seal), wordmark=_png_uri(wordmark))


@lru_cache(maxsize=8)
def _mark_uri(path: str, modified: float) -> str | None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            image = image.convert("RGBA")
            image.thumbnail((MARK_HEIGHT_PX * 3, MARK_HEIGHT_PX))
            return _png_uri(image)
    except (OSError, UnidentifiedImageError, ValueError):
        return None


def _stamp(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def logo_parts() -> LogoParts | None:
    path = Path(settings.LETTERHEAD_DIR) / LOGO_FILE
    modified = _stamp(path)
    return _logo_parts(str(path), modified) if modified is not None else None


def marks() -> list[dict]:
    """The official marks whose files exist (none until NAVIGATU gives them)."""
    found = []
    for name, alt in MARKS:
        path = Path(settings.LETTERHEAD_DIR) / name
        modified = _stamp(path)
        if modified is not None and (uri := _mark_uri(str(path), modified)):
            found.append({"uri": uri, "alt": alt})
    return found


def css_string(value: str) -> SafeString:
    """A value for a CSS `content: "..."` string inside <style>: quotes, backslashes, line breaks and "<" escaped, so
    nothing can end the string or the style element."""
    escaped = (str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\A ").replace("\r", "")
               .replace("<", "\\3C ").replace(">", "\\3E "))
    return mark_safe(escaped)


def context(*, office_line: str = "", label: str = "", form_code: str = "", footer_left: str = "",
            compact: bool = False, sample: bool | None = None) -> dict:
    """What `pdf/_letterhead.html` and `pdf/_letterhead_styles.html` need. `office_line` empty = the default
    (LETTERHEAD_OFFICE_LINE); `label` = a line under it on every page ("ACTIVITY REPORT"); `footer_left` = the bottom
    left text when there is no form code; `compact` = the short header of the landscape figures reports; `sample` = True
    prints the sample line whatever LETTERHEAD_SAMPLE_LINE says (the seed_defense files, ASSUMPTIONS 470)."""
    parts = logo_parts()
    show_sample = settings.LETTERHEAD_SAMPLE_LINE if sample is None else sample
    left = form_code or footer_left
    return {
        "seal": parts.seal if parts else "",
        "wordmark": parts.wordmark if parts else "",
        "marks": marks(),
        "republic": REPUBLIC,
        "university": UNIVERSITY,
        "address": settings.LETTERHEAD_ADDRESS,
        "motto": [(word[0], word[1:]) for word in MOTTO],
        "office_line": " ".join((office_line or settings.LETTERHEAD_OFFICE_LINE).split()).upper(),
        "label": label,
        "footer_left": css_string(left) if left else "",
        "sample": css_string(settings.LETTERHEAD_SAMPLE_TEXT) if show_sample else "",
        "sample_text": settings.LETTERHEAD_SAMPLE_TEXT if show_sample else "",
        "compact": compact,
    }


def logo_png() -> bytes | None:
    """The whole logo as a PNG (the DOCX header), or None without the file."""
    path = Path(settings.LETTERHEAD_DIR) / LOGO_FILE
    try:
        return path.read_bytes()
    except OSError:
        return None
