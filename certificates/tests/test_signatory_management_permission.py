"""Only can_manage_signatories manages the signatory list (ASSUMPTIONS 418, NAVIGATU: only the TBI Director): add, edit,
signature images (new versions), "Remove white background", deactivate, the defaults, the Signatories page, the
Certificates tab's signature upload and "Add a person" (also on the activity form), the JSON API. Everyone else gets
403, also the certificate approvers. Choosing an activity's signatories from the list and approving certificates are
unchanged."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from accounts.permissions import Permission, has_permission
from activities import services as activity_services
from certificates import services
from certificates.models import CertificateSignatory
from conftest import VIEW_ONLY_ROLE, image_bytes

from .conftest import upload

pytestmark = pytest.mark.django_db

NOT_MANAGERS = ["Incubation Project Head", "Community Development Head", "Admin Staff", Role.SYSTEM_ADMINISTRATOR,
                VIEW_ONLY_ROLE]  # the first four approve certificates (ASSUMPTIONS 361, 388)
NOTE = "Only staff who manage signatories can add or replace signature images."


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def png(name: str = "sig.png"):
    return upload(name, image_bytes(40, 20))


@pytest.fixture
def activity(director, valid_data, design):
    """A Draft with the default signatories (the design's Director on the left, the VP on the right)."""
    return activity_services.create_activity(director, valid_data(title="Signatory rules"))


def service_calls(design, activity) -> dict:
    """Every signatory-list service, as a call taking only the member."""
    _, vp, head = design
    return {
        "add_signatory": lambda m: services.add_signatory(m, "Dr. New Person", "Dean", png()),
        "edit_signatory": lambda m: services.edit_signatory(m, vp, "Dr. Vice Pres", "Vice President for Research"),
        "replace_signature": lambda m: services.replace_signature(m, vp, png()),
        "remove_signature_background": lambda m: services.remove_signature_background(m, vp),
        "deactivate_signatory": lambda m: services.deactivate_signatory(m, vp),
        "set_default_signatories": lambda m: services.set_default_signatories(m, vp, head),
        "signature_preview": lambda m: services.signature_preview(m, png()),
        "upload_signature": lambda m: services.upload_signature(m, activity, "left", png()),
    }


def page_requests(design, activity) -> list[tuple[str, str, dict]]:
    """(method, url, data) of every signatory-list page and endpoint."""
    _, vp, head = design
    return [
        ("get", reverse("certificates:signatories"), {}),
        ("post", reverse("certificates:signatory_add"), {"name": "Dr. New Person", "position": "Dean"}),
        ("post", reverse("certificates:signatory_add_inline"), {"name": "Dr. New Person", "position": "Dean"}),
        ("post", reverse("certificates:signature_preview"), {"file": png()}),
        ("post", reverse("certificates:signatory_defaults"), {"left": head.pk, "right": vp.pk}),
        ("post", reverse("certificates:signatory_edit", args=[vp.pk]), {"name": "Dr. V", "position": "VP"}),
        ("post", reverse("certificates:signatory_image", args=[vp.pk]), {"file": png()}),
        ("post", reverse("certificates:signatory_remove_background", args=[vp.pk]), {}),
        ("post", reverse("certificates:signatory_deactivate", args=[vp.pk]), {}),
        ("post", reverse("activities:certificates_signature", args=[activity.pk, "left"]), {"file": png()}),
        ("get", reverse("certificates:api_signatories"), {}),
        ("post", reverse("certificates:api_signatories"), {"name": "Dr. New Person", "position": "Dean"}),
        ("post", reverse("certificates:api_signatory_deactivate", args=[vp.pk]), {}),
    ]


# --- The services --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role_name", NOT_MANAGERS)
def test_every_signatory_service_refuses_everyone_else(make_member, design, activity, role_name) -> None:
    member = make_member(role_name)
    before = list(CertificateSignatory.objects.order_by("pk").values())
    for name, call in service_calls(design, activity).items():
        with pytest.raises(PermissionDenied, match="manage certificate signatories"):
            call(member)
    assert list(CertificateSignatory.objects.order_by("pk").values()) == before  # nothing changed


@pytest.mark.parametrize("name", ["add_signatory", "edit_signatory", "replace_signature", "remove_signature_background",
                                  "deactivate_signatory", "set_default_signatories", "signature_preview",
                                  "upload_signature"])
def test_the_director_passes_the_permission_of_each(director, design, activity, name) -> None:
    """Each service on a fresh design: the permission lets the Director through (a business rule of the service may
    still refuse, e.g. no white background to remove or an active default; that is not a permission refusal)."""
    assert has_permission(director, Permission.MANAGE_SIGNATORIES)
    try:
        service_calls(design, activity)[name](director)
    except PermissionDenied:  # pragma: no cover - the failure this test looks for
        pytest.fail(f"{name} refused the Director")
    except (ValidationError, services.CertificateError):
        pass


def test_the_director_adds_a_person_and_sets_the_defaults(director, design) -> None:
    _, _, head = design
    person = services.add_signatory(director, "Dr. New Person", "Dean", png())
    services.set_default_signatories(director, head, person)
    assert services.default_signatories() == {"left": head, "right": person}


# --- The pages and the JSON API -------------------------------------------------------------------------------------


@pytest.mark.parametrize("role_name", NOT_MANAGERS)
def test_every_signatory_page_and_endpoint_answers_403(make_member, design, activity, role_name) -> None:
    client = client_for(make_member(role_name))
    before = CertificateSignatory.objects.count()
    for method, url, data in page_requests(design, activity):
        response = getattr(client, method)(url, data)
        assert response.status_code == 403, (role_name, url)
    assert CertificateSignatory.objects.count() == before


def test_the_director_opens_the_signatories_page_and_adds_a_person(director, design) -> None:
    client = client_for(director)
    assert client.get(reverse("certificates:signatories")).status_code == 200
    client.post(reverse("certificates:signatory_add"), {"name": "Dr. Added", "position": "Dean", "file": png()})
    assert CertificateSignatory.objects.filter(name="Dr. Added", is_active=True).exists()
    assert client.get(reverse("certificates:api_signatories")).status_code == 200


# --- What each member sees ----------------------------------------------------------------------------------------


def test_the_certificates_tab_offers_signature_upload_only_to_the_director(director, make_member, design, activity) -> None:
    services.upload_signature(director, activity, "left", png())  # keep both images; then remove one to see the note
    _, vp, _ = design
    CertificateSignatory.objects.filter(pk=vp.pk).update(signature_image_path=None)
    url = reverse("activities:detail", args=[activity.pk]) + "?tab=certificates"
    mine = client_for(director).get(url).content.decode()
    link = f'href="{reverse("certificates:signatories")}"'  # "All signatories" (the choose URLs contain the path too)
    assert "Add signature" in mine and "Add a person" in mine and link in mine
    assert NOTE not in mine
    head = client_for(make_member("Incubation Project Head")).get(url).content.decode()
    assert "Add signature" not in head and "Replace signature" not in head and "Add a person" not in head
    assert link not in head and NOTE in head
    assert 'name="signatory"' not in head and "Only the Director chooses who signs." in head  # ASSUMPTIONS 431


def test_the_activity_form_offers_add_a_person_only_to_the_director(director, make_member, design) -> None:
    url = reverse("activities:create")
    assert "Add a person…" in client_for(director).get(url).content.decode()
    head_page = client_for(make_member("Incubation Project Head")).get(url).content.decode()
    assert "Add a person…" not in head_page and 'name="signatory_left"' not in head_page  # read only (431)


def test_the_certificates_page_links_the_signatories_page_only_for_the_director(director, make_member) -> None:
    link = reverse("certificates:signatories")
    assert link in client_for(director).get(reverse("certificates:list")).content.decode()
    assert link not in client_for(make_member("Admin Staff")).get(reverse("certificates:list")).content.decode()


def test_approvers_still_see_the_signature_images(make_member, design) -> None:
    """They check the signatures before approving (ASSUMPTIONS 361); others still cannot."""
    _, vp, _ = design
    url = reverse("certificates:signature_image", args=[vp.pk])
    assert client_for(make_member("Incubation Project Head")).get(url).status_code == 200
    assert client_for(make_member(VIEW_ONLY_ROLE)).get(url).status_code == 403


# --- Unchanged: choosing an activity's signatories, approving ------------------------------------------------------


def test_choosing_from_the_list_keeps_its_rule(director, make_member, design, activity) -> None:
    _, _, head = design
    dean = services.add_signatory(director, "Dr. Dean", "Dean", png())  # the list: the Director's
    services.set_activity_signatory(director, activity, "left", dean)  # ASSUMPTIONS 431: the Director chooses
    services.set_activity_signatory(director, activity, "left", head)
    assert services.activity_signatories(activity)["left"] == head
    for role_name in ("Incubation Project Head", "Admin Staff", VIEW_ONLY_ROLE):
        with pytest.raises(PermissionDenied):
            services.set_activity_signatory(make_member(role_name), activity, "left", dean)


def test_certificate_approval_still_needs_only_can_approve_certificates(make_member) -> None:
    head = make_member("Incubation Project Head")
    assert not has_permission(head, Permission.APPROVE_CERTIFICATES)  # the Director only since ASSUMPTIONS 421
    assert not has_permission(head, Permission.MANAGE_SIGNATORIES)
    director = make_member("Director", email="dir.flags@example.com")
    assert has_permission(director, Permission.APPROVE_CERTIFICATES) and has_permission(director, Permission.MANAGE_SIGNATORIES)
