"""Stamping the Director's signature image on a copy of a submitted PDF (ASSUMPTIONS 437).

This is a STAMPED IMAGE of a signature, not a cryptographic digital signature (no certificate, nothing can be verified
from the file). pypdf only (already used by the assistant), no new library: the image becomes a PDF image object that is
drawn on the chosen page of a COPY; the original bytes are never changed.

Placement: at the last "Noted:" in the PDF's text (found with pypdf's text extraction; the image goes just below and to
the right of the word, where the signature line usually is), or on a chosen page at one of six positions. A scanned PDF
has no text, so "Noted:" is never found there: the Director chooses a page and a position. Encrypted PDFs are refused.
"""

import zlib
from dataclasses import dataclass
from datetime import date
from io import BytesIO

from PIL import Image
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

NOTED_WORD = "noted"
NOTED = "noted"  # the placement value for "at the Noted: line"
POSITIONS = {  # (horizontal, vertical) as fractions of the free area inside the margins; vertical 0 = bottom
    "bottom-left": (0.0, 0.0), "bottom-center": (0.5, 0.0), "bottom-right": (1.0, 0.0),
    "middle-left": (0.0, 0.5), "middle-center": (0.5, 0.5), "middle-right": (1.0, 0.5),
}
POSITION_LABELS = {
    "bottom-left": "Bottom left", "bottom-center": "Bottom centre", "bottom-right": "Bottom right",
    "middle-left": "Middle left", "middle-center": "Middle centre", "middle-right": "Middle right",
}
MARGIN_PT = 54  # 0.75 inch from the page edge
STAMP_MAX_WIDTH_PT = 130  # about 46 mm
STAMP_MAX_HEIGHT_PT = 48  # about 17 mm
CAPTION_SIZE_PT = 5.5
NOTED_GAP_PT = 2  # the image's top edge below the "Noted:" baseline
NOTED_INDENT_PT = 12

ENCRYPTED = "This PDF is password-protected, so it cannot be signed. Approve it without signing, or ask for an unprotected PDF."
UNREADABLE = "This PDF cannot be read, so it cannot be signed. Approve it without signing."
NO_NOTED = 'No "Noted:" line was found in this PDF (a scanned PDF has no text). Choose a page and a position instead.'
BAD_PAGE = "Choose a page between 1 and {pages}."
BAD_POSITION = "Choose where the signature goes."
NO_IMAGE = "Your signature image could not be read. Ask the member who manages signatories to upload it again."


class SigningError(Exception):
    """Signing was refused; the message is safe to show."""


@dataclass(frozen=True)
class Placement:
    """Where the stamp goes: `noted` (the last "Noted:"), or `page` (1-based) + `position` (a POSITIONS key)."""

    how: str
    page: int = 1
    position: str = "bottom-right"


def open_pdf(data: bytes) -> PdfReader:
    """A pypdf reader for the bytes, or SigningError (encrypted, unreadable)."""
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise SigningError(ENCRYPTED)
        if not len(reader.pages):
            raise SigningError(UNREADABLE)
    except SigningError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as error:
        raise SigningError(UNREADABLE) from error
    return reader


def find_noted(reader: PdfReader) -> tuple[int, float, float] | None:
    """(page index, x, y) of the LAST "Noted:" in the text (case does not matter), or None. x, y are in the page's own
    coordinates (points), the start of the word's baseline."""
    found = None
    for index, page in enumerate(reader.pages):
        hits = []

        def visitor(text, cm, tm, font_dict, font_size, hits=hits):
            if NOTED_WORD in (text or "").lower():
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                hits.append((float(x), float(y)))

        try:
            page.extract_text(visitor_text=visitor)
        except Exception:  # a broken content stream: no text on that page  # noqa: BLE001
            continue
        if hits:
            found = (index, *min(hits, key=lambda hit: (hit[1], -hit[0])))  # the lowest one on the page
    return found


def page_count(data: bytes) -> int:
    return len(open_pdf(data).pages)


def noted_page(data: bytes) -> int | None:
    """The page (1-based) with the last "Noted:", or None (no text, or not there). For the approve dialog."""
    try:
        found = find_noted(open_pdf(data))
    except SigningError:
        return None
    return found[0] + 1 if found else None


def _image_objects(image_bytes: bytes) -> tuple[DecodedStreamObject, DecodedStreamObject | None, int, int]:
    """The signature as a PDF image (RGB, Flate) and its transparency mask (from the alpha channel), or SigningError."""
    try:
        image = Image.open(BytesIO(image_bytes))
        image.load()
    except Exception as error:  # noqa: BLE001
        raise SigningError(NO_IMAGE) from error
    has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    image = image.convert("RGBA" if has_alpha else "RGB")
    width, height = image.size

    def stream(raw: bytes, colour_space: str) -> DecodedStreamObject:
        obj = DecodedStreamObject()
        obj.set_data(zlib.compress(raw))
        obj.update({
            NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(width), NameObject("/Height"): NumberObject(height),
            NameObject("/ColorSpace"): NameObject(colour_space), NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/FlateDecode"),
        })
        return obj

    rgb = stream(image.convert("RGB").tobytes(), "/DeviceRGB")
    mask = stream(image.getchannel("A").tobytes(), "/DeviceGray") if has_alpha else None
    return rgb, mask, width, height


def _stamp_size(width_px: int, height_px: int) -> tuple[float, float]:
    scale = min(STAMP_MAX_WIDTH_PT / width_px, STAMP_MAX_HEIGHT_PT / height_px)
    return width_px * scale, height_px * scale


def _origin(reader: PdfReader, placement: Placement, size: tuple[float, float]) -> tuple[int, float, float]:
    """(page index, x, y) of the stamp's lower-left corner in the page's coordinates."""
    width, height = size
    if placement.how == NOTED:
        found = find_noted(reader)
        if found is None:
            raise SigningError(NO_NOTED)
        index, x, y = found
        return index, x + NOTED_INDENT_PT, y - NOTED_GAP_PT - height
    pages = len(reader.pages)
    if not 1 <= placement.page <= pages:
        raise SigningError(BAD_PAGE.format(pages=pages))
    if placement.position not in POSITIONS:
        raise SigningError(BAD_POSITION)
    box = reader.pages[placement.page - 1].mediabox
    across, up = POSITIONS[placement.position]
    free_x = max(0.0, float(box.width) - 2 * MARGIN_PT - width)
    free_y = max(0.0, float(box.height) - 2 * MARGIN_PT - height)
    return placement.page - 1, float(box.left) + MARGIN_PT + across * free_x, float(box.bottom) + MARGIN_PT + up * free_y


def _pdf_text(value: str) -> str:
    """A PDF literal string (Helvetica, plain ASCII)."""
    value = value.encode("ascii", "replace").decode("ascii")
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def stamp(pdf_bytes: bytes, image_bytes: bytes, placement: Placement, caption_date: date) -> bytes:
    """A COPY of `pdf_bytes` with the signature image drawn at `placement` and a small grey caption under it
    ("Stamped in CSU-TBI Track, Nov 25, 2026"). The input bytes are never changed. SigningError when it cannot be done."""
    reader = open_pdf(pdf_bytes)
    rgb, mask, width_px, height_px = _image_objects(image_bytes)
    size = _stamp_size(width_px, height_px)
    index, x, y = _origin(reader, placement, size)

    overlay_writer = PdfWriter()
    box = reader.pages[index].mediabox
    overlay = overlay_writer.add_blank_page(width=float(box.right), height=float(box.top))
    if mask is not None:
        rgb[NameObject("/SMask")] = overlay_writer._add_object(mask)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    overlay[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/TBISignature"): overlay_writer._add_object(rgb)}),
        NameObject("/Font"): DictionaryObject({NameObject("/TBIStampFont"): overlay_writer._add_object(font)}),
        NameObject("/ProcSet"): ArrayObject([NameObject("/PDF"), NameObject("/Text"), NameObject("/ImageC")]),
    })
    caption = _pdf_text(f"Stamped in CSU-TBI Track, {caption_date:%b} {caption_date.day}, {caption_date.year}")
    content = DecodedStreamObject()
    content.set_data((
        f"q {size[0]:.2f} 0 0 {size[1]:.2f} {x:.2f} {y:.2f} cm /TBISignature Do Q\n"
        f"q 0.45 g BT /TBIStampFont {CAPTION_SIZE_PT} Tf {x:.2f} {y - CAPTION_SIZE_PT - 1:.2f} Td ({caption}) Tj ET Q\n"
    ).encode("ascii"))
    overlay[NameObject("/Contents")] = overlay_writer._add_object(content)
    buffer = BytesIO()
    overlay_writer.write(buffer)
    overlay_page = PdfReader(BytesIO(buffer.getvalue())).pages[0]

    writer = PdfWriter(clone_from=reader)
    writer.pages[index].merge_transformed_page(overlay_page, Transformation())
    out = BytesIO()
    writer.write(out)
    return out.getvalue()
