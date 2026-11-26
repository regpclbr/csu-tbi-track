"""Owner feedback before the user testing, Part 1 (ASSUMPTIONS 450-459): the small UI rules.

- 450 Enter never submits a marked long staff form (static/js/form_keys.js + data-enter-submit="off");
- 453 the three signature choices (public form, check-in, staff walk-in);
- 456 no Director-to-self actions ("Submit to the Director", "Assigned to me");
- 457 the certificate number and name open its preview;
- 459 the green theme (tokens, PDFs, Excel, DOCX headings, logo tiles).
"""

import io
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

BASE = Path(settings.BASE_DIR)


@pytest.fixture
def director(make_member):
    return make_member("Director")


# --- 450: the Enter key --------------------------------------------------------------------------------------------------

MARKED = [
    "templates/activities/form.html", "templates/registrations/_field_editor.html",
    "templates/certificates/_text_section.html", "templates/certificates/text.html",
    "templates/reports/_generator_form.html", "templates/approvals/submit.html", "templates/approvals/_assign.html",
    "templates/certificates/_add_person_form.html", "templates/certificates/_edit_person_form.html",
    "templates/certificates/_add_person_inline_form.html", "templates/reports/paper_sheet.html",
    "templates/registrations/walk_in.html",
]
NOT_MARKED = ["accounts/templates/accounts/login.html", "templates/partials/topbar.html", "templates/pages/search.html",
              "templates/public/register.html", "templates/public/checkin.html"]


def test_the_long_staff_forms_are_marked_and_login_and_search_are_not() -> None:
    for path in MARKED:
        assert 'data-enter-submit="off"' in (BASE / path).read_text(encoding="utf-8"), path
    for path in NOT_MARKED:
        assert 'data-enter-submit' not in (BASE / path).read_text(encoding="utf-8"), path


def test_the_script_is_self_hosted_and_loaded_on_every_page(client, director) -> None:
    script = (BASE / "static/js/form_keys.js").read_text(encoding="utf-8")
    assert 'event.key !== "Enter"' in script and "textarea: true" in script and 'data-enter-submit' in script
    client.force_login(director)
    page = client.get(reverse("activities:create")).content.decode()
    assert "js/form_keys.js" in page and 'data-enter-submit="off"' in page


# --- 453: the signature choices --------------------------------------------------------------------------------------


def test_three_equal_signature_choices_with_help_text() -> None:
    from django.template.loader import render_to_string

    html = render_to_string("public/_signature_field.html", {"values": {}})
    assert [m for m in re.findall(r'data-signature-choice="(\w+)"', html)] == ["draw", "upload", "typed"]
    for text in ("Draw", "Upload a picture", "Type my name", "A photo of your signature on paper"):
        assert text in html
    assert "sm:grid-cols-3" in html
    staff = render_to_string("public/_signature_field.html", {"values": {}, "staff": True})
    assert re.findall(r'data-signature-choice="(\w+)"', staff) == ["draw", "typed"]  # the staff dialog: unchanged rule


# --- 456: no Director-to-self actions --------------------------------------------------------------------------------


def test_the_director_does_not_submit_to_themselves(client, director, make_member) -> None:
    from approvals import services as approval_services
    from approvals.assignments import shows_assigned_to_me

    staff = make_member("Admin Staff")
    assert approval_services.offers_submit(staff) and not approval_services.offers_submit(director)
    assert shows_assigned_to_me(staff) and not shows_assigned_to_me(director)
    client.force_login(director)
    page = client.get(reverse("approvals:mine")).content.decode()
    assert "Submit to the Director" not in page and "Assigned to me" not in page
    client.force_login(staff)
    page = client.get(reverse("approvals:mine")).content.decode()
    assert "Submit to the Director" in page and "Assigned to me" in page


def test_a_second_approver_may_still_submit_to_the_director(make_member, director) -> None:
    """The rule is "somebody else could approve it", the same as the service's refusal (separation of duties)."""
    from accounts.models import Role
    from approvals import services as approval_services

    second = make_member("Director", email="stand-in@example.com")
    assert Role.objects.get(role_name="Director").can_approve_documents
    assert approval_services.offers_submit(director) and approval_services.offers_submit(second)


def test_the_director_sees_assigned_to_me_once_something_was_assigned(make_member, director) -> None:
    from accounts.models import Role
    from approvals.assignments import shows_assigned_to_me
    from approvals.models import WorkAssignment

    other = make_member("Director", email="assigner@example.com")
    WorkAssignment.objects.create(kind=WorkAssignment.Kind.REPORT, report_title="Q3 report", assigned_to=director,
                                  assigned_by=other)
    assert shows_assigned_to_me(director) and Role.objects.get(role_name="Director").can_assign_work


# --- 457: the certificate number and the name open the preview ---------------------------------------------------------


def test_the_number_and_the_name_link_to_the_preview() -> None:
    table = (BASE / "templates/certificates/_table.html").read_text(encoding="utf-8")
    tab = (BASE / "templates/certificates/_tab_certificates.html").read_text(encoding="utf-8")
    assert table.count("{% url 'certificates:preview' certificate.pk %}") == 3  # number, name, the Preview button
    assert table.count("data-preview-link") == 2
    assert tab.count("$dispatch('certificate-preview', {{ certificate.pk }})") == 3  # number, name, Preview
    assert tab.count("data-preview-link") == 2


# --- 459: green --------------------------------------------------------------------------------------------------------

GREEN = "#1A5C38"


def test_the_tokens_are_green_and_the_built_css_follows() -> None:
    source = (BASE / "frontend/tailwind/app.css").read_text(encoding="utf-8")
    for token, value in (("frame-dark", "#0B3D24"), ("frame-light", "#24603F"), ("primary", GREEN), ("link", "#1E6B42")):
        assert re.search(rf"--color-{token}:\s*{value};", source), token
    css = (BASE / "static/css/app.css").read_text(encoding="utf-8").lower()
    assert "--color-frame-dark:#0b3d24" in css and "--color-primary:#1a5c38" in css and "2a4494" not in css


def test_the_report_pdfs_excel_and_docx_are_green() -> None:
    from reports import excel, pdf

    for path in ("reports/templates/reports/report_pdf.html", "reports/templates/reports/draft_pdf.html",
                 "reports/templates/reports/individual_evaluation_pdf.html",
                 "templates/registrations/attendance_forms_pdf.html"):
        text = (BASE / path).read_text(encoding="utf-8")
        assert GREEN in text and "#2A4494" not in text.upper(), path
    assert pdf.BAR_COLOR == GREEN and excel.BRAND_NAVY == GREEN[1:]
    from reports import draft_export

    assert draft_export.DOCX_HEADING_COLOR == GREEN[1:]


def test_the_logo_tiles_are_on_the_dark_green() -> None:
    from PIL import Image

    for name in ("favicon-32", "logo-96", "apple-touch-icon-180"):
        with Image.open(BASE / f"static/img/{name}.png") as image:
            assert image.convert("RGB").getpixel((0, 0)) == (0x0B, 0x3D, 0x24), name
