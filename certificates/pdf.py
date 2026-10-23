"""Certificate PDFs. The ONLY module that calls WeasyPrint (like assistant/gemini.py for Gemini).

- `certificate_html()` builds the print HTML (templates/certificates/certificate_pdf.html).
- `get_renderer()` is the single injection point: WeasyPrint in the app, a fake in tests
  (root conftest), so tests never need Pango.
- Images (template background, signatures) are embedded as data: URIs, and WeasyPrint may fetch
  only data: URIs, so text on a certificate can never make it load a file or a web address.
"""

import base64
import mimetypes

from django.conf import settings
from django.template.loader import render_to_string

from .models import Certificate

PREVIEW_NAME_NOTE = "(name from evaluation)"


class WeasyPrintRenderer:
    def render(self, html: str) -> bytes:
        import weasyprint  # imported here: a missing Pango only fails rendering, never Django startup

        fetcher = weasyprint.URLFetcher(allowed_protocols=["data"])
        return weasyprint.HTML(string=html, url_fetcher=fetcher).write_pdf()


_override = None  # tests put a fake renderer here


def get_renderer():
    return _override or WeasyPrintRenderer()


def _data_uri(field_file) -> str:
    """The stored image as a data: URI (read from local storage)."""
    with field_file.open("rb") as handle:
        content = handle.read()
    mime = mimetypes.guess_type(field_file.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"


def long_date(value) -> str:
    """November 5, 2026 (a certificate spells the month out)."""
    return f"{value:%B} {value.day}, {value.year}"


def _dates(start, end) -> str:
    return long_date(start) if start == end else f"{long_date(start)} to {long_date(end)}"


def _name_size(name: str) -> str:
    """A CSS class so long names still fit on one or two lines."""
    return "name-long" if len(name) > 45 else "name-medium" if len(name) > 30 else "name-short"


def certificate_html(certificate: Certificate, *, name: str, preview: bool) -> str:
    """The certificate as print HTML. A preview has a watermark and no signature images."""
    activity = certificate.activity
    is_participant = certificate.recipient_type == Certificate.RecipientType.REGISTRATION
    topic = ""
    if not is_participant:
        link = activity.resource_person_links.filter(resource_person_id=certificate.resource_person_id).first()
        topic = link.topic if link else ""
    director, vp = certificate.director_signatory, certificate.vp_signatory
    context = {
        "title": "Certificate of Participation" if is_participant else "Certificate of Appreciation",
        "is_participant": is_participant,
        "name": name,
        "name_note": PREVIEW_NAME_NOTE if preview and is_participant else "",
        "name_size": _name_size(name),
        "activity_title": activity.title,
        "dates": _dates(activity.start_date, activity.end_date),
        "venue": activity.venue,
        "topic": topic,
        "given_on": long_date(activity.end_date),
        "organization": settings.CERTIFICATE_ORGANIZATION_NAME,
        "number": certificate.certificate_number,
        "background": _data_uri(certificate.certificate_template.file_path),
        "signatories": [
            {
                "name": person.name,
                "position": person.get_position_display(),
                "signature": None if preview else _data_uri(person.signature_image_path),
            }
            for person in (director, vp)
        ],
        "preview": preview,
    }
    return render_to_string("certificates/certificate_pdf.html", context)


def render_certificate_pdf(certificate: Certificate, *, name: str, preview: bool = False) -> bytes:
    pdf = get_renderer().render(certificate_html(certificate, name=name, preview=preview))
    if not pdf.startswith(b"%PDF-"):
        raise RuntimeError("The PDF renderer returned something that is not a PDF.")
    return pdf
