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


class ImageUnreadable(Exception):
    """An image the final PDF needs could not be read. The message is plain words, safe to store and show
    (CERTIFICATE.render_error, ASSUMPTIONS 368)."""


class NotAPdf(RuntimeError):
    """The renderer returned something that is not a PDF."""


class WeasyPrintRenderer:
    def render(self, html: str) -> bytes:
        import weasyprint  # imported here: a missing Pango only fails rendering, never Django startup

        fetcher = weasyprint.URLFetcher(allowed_protocols=["data"])
        return weasyprint.HTML(string=html, url_fetcher=fetcher).write_pdf()


_override = None  # tests put a fake renderer here


def get_renderer():
    return _override or WeasyPrintRenderer()


def _signature_uri(signatory, missing_ok: bool) -> str | None:
    """The signature as a data: URI. missing_ok (previews only): a file that cannot be read is left blank instead of
    failing; approval is refused in that case anyway (services.missing_signature_reason)."""
    try:
        return _data_uri(signatory.signature_image_path)
    except (FileNotFoundError, OSError, ValueError) as error:
        if missing_ok:
            return None
        raise ImageUnreadable(f"The signature image of {signatory.name} could not be read.") from error


def _background_uri(template) -> str:
    try:
        return _data_uri(template.file_path)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise ImageUnreadable("The certificate design image could not be read.") from error


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


def certificate_html(certificate: Certificate, *, name: str, mark: bool = False, missing_signature_ok: bool = False) -> str:
    """The certificate as print HTML: the same for the preview and the final PDF (ASSUMPTIONS 358). `mark` adds a small
    "PREVIEW" in a corner (a preview of a certificate still waiting for approval)."""
    activity = certificate.activity
    is_participant = certificate.recipient_type == Certificate.RecipientType.REGISTRATION
    topic = ""
    if not is_participant:
        link = activity.resource_person_links.filter(resource_person_id=certificate.resource_person_id).first()
        topic = link.topic if link else ""
    context = {
        "title": "Certificate of Participation" if is_participant else "Certificate of Appreciation",
        "is_participant": is_participant,
        "name": name,
        "name_size": _name_size(name),
        "activity_title": activity.title,
        "dates": _dates(activity.start_date, activity.end_date),
        "venue": activity.venue,
        "topic": topic,
        "given_on": long_date(activity.end_date),
        "organization": settings.CERTIFICATE_ORGANIZATION_NAME,
        "number": certificate.certificate_number,
        "background": _background_uri(certificate.certificate_template),
        "signatories": [
            {
                "name": person.name,
                # The title stored on the certificate: the activity's "Title on this certificate", else the person's
                # position (ASSUMPTIONS 365). An Issued certificate keeps the one it was printed with.
                "position": title or person.position,
                "signature": _signature_uri(person, missing_signature_ok),
            }
            # Printed left to right: the activity's signatories stored on the certificate (ASSUMPTIONS 363).
            for person, title in ((certificate.left_signatory, certificate.left_signatory_title),
                                  (certificate.right_signatory, certificate.right_signatory_title))
        ],
        "mark": mark,
    }
    return render_to_string("certificates/certificate_pdf.html", context)


def render_certificate_pdf(certificate: Certificate, *, name: str, mark: bool = False, missing_signature_ok: bool = False) -> bytes:
    html = certificate_html(certificate, name=name, mark=mark, missing_signature_ok=missing_signature_ok)
    pdf = get_renderer().render(html)
    if not pdf.startswith(b"%PDF-"):
        raise NotAPdf("The PDF renderer returned something that is not a PDF.")
    return pdf
