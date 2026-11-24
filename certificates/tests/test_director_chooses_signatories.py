"""Only the Director chooses who signs (ASSUMPTIONS 431, NAVIGATU owner decision 2026-11-24): the left / right signatories
and their "Title on this certificate" need can_approve_certificates, in the service (every path: Certificates tab, edit
form, New form, any API) and on the pages (read only with "Only the Director chooses who signs."). Staff who create
activities still prepare the certificates, edit the activity's text and see the previews; a text change after the
approval sends the certificates back to waiting for approval."""

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from activities import services as activity_services
from certificates import pages, services
from conftest import typed_schedule_fields

pytestmark = pytest.mark.django_db

CREATORS = ["Incubation Project Head", "Community Development Head", "Admin Staff"]
P = services.TEXT_PARTICIPATION
ONLY = "Only the Director chooses who signs."


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def form_post(valid_data, **overrides) -> dict:
    """The New / Edit activity form's POST data, as the browser sends it."""
    extra = {key: overrides.pop(key) for key in list(overrides) if key.startswith(("signatory_", "title_", "prepare"))}
    values = valid_data(**overrides)
    return {
        "title": values["title"], "description": values["description"], "activity_type": values["activity_type"].pk,
        "participant_categories": [c.pk for c in values["participant_categories"]],
        **typed_schedule_fields(values), "venue": values["venue"], "max_slots": values["max_slots"],
        "fund_source": values["fund_source"].pk, **extra,
    }


@pytest.fixture
def other(director):
    return services.add_signatory(director, "Dr. Other Person", "Dean")


def slots(activity) -> dict:
    return {slot: (row.signatory_id, row.position_on_certificate) for slot, row in services.activity_slots(activity).items()}


# --- The service ----------------------------------------------------------------------------------------------------


def test_the_rule_is_the_certificate_approval_flag() -> None:
    assert services.CHOOSE_SIGNATORIES_PERMISSION == services.Permission.APPROVE_CERTIFICATES
    assert services.ONLY_DIRECTOR_CHOOSES == ONLY


@pytest.mark.parametrize("role_name", CREATORS)
def test_every_signatory_service_refuses_a_non_director(new_activity, design, other, make_member, role_name) -> None:
    member = make_member(role_name)
    before = slots(new_activity)
    with pytest.raises(PermissionDenied, match=ONLY):
        services.set_activity_signatory(member, new_activity, "right", other)
    with pytest.raises(PermissionDenied, match=ONLY):
        services.set_activity_signatory(member, new_activity, "left", design[2], title="Director, CSU-TBI")  # title only
    with pytest.raises(PermissionDenied, match=ONLY):
        services.set_activity_signatories(member, new_activity, {"right": (other, "")})
    with pytest.raises(PermissionDenied, match=ONLY):
        services.use_current_version(member, new_activity, "left")
    with pytest.raises(PermissionDenied, match=ONLY):
        activity_services.update_activity(member, new_activity, {}, signatories={"right": (other, "")})
    assert slots(new_activity) == before and not services.can_choose_signatories(member)


def test_the_director_chooses(new_activity, design, other, director) -> None:
    assert services.can_choose_signatories(director)
    services.set_activity_signatory(director, new_activity, "right", other, title="Dean, CSU")
    assert slots(new_activity)["right"] == (other.pk, "Dean, CSU")


@pytest.mark.parametrize("role_name", CREATORS)
def test_create_with_a_choice_is_refused_without_a_choice_gets_the_defaults(design, other, make_member, valid_data,
                                                                            role_name) -> None:
    _, vp, head = design
    member = make_member(role_name)
    with pytest.raises(PermissionDenied, match=ONLY):
        activity_services.create_activity(member, valid_data(title="Chosen"), signatories={"right": (other, "")},
                                          prepare_certificates=True)
    with pytest.raises(PermissionDenied, match=ONLY):  # a title alone is a choice too
        activity_services.create_activity(member, valid_data(title="Titled"), signatories={"left": (None, "Boss")},
                                          prepare_certificates=True)
    activity = activity_services.create_activity(member, valid_data(title="Defaults"),
                                                 signatories={"left": (None, ""), "right": (None, "")},
                                                 prepare_certificates=True)
    assert slots(activity) == {"left": (head.pk, None), "right": (vp.pk, None)}
    assert services.is_prepared(activity)  # preparing is still theirs (ASSUMPTIONS 420)


# --- Text editing and the approval ----------------------------------------------------------------------------------


@pytest.mark.parametrize("role_name", CREATORS)
def test_text_editing_is_still_allowed_and_cancels_the_approval(new_activity, design, director, make_member,
                                                               role_name) -> None:
    member = make_member(role_name)
    services.prepare_activity_certificates(member, new_activity)
    services.approve_activity_certificates(director, new_activity)
    new_activity.refresh_from_db()
    assert services.is_approved(new_activity)
    assert services.can_edit_activity_text(member)
    values = services.text_values(services.current_texts(new_activity)[P])
    result = services.save_activity_text(member, new_activity, {P: {**values, "title": "Certificate of Attendance"}})
    new_activity.refresh_from_db()
    assert result["changed"] and result["cancelled"] and not services.is_approved(new_activity)  # back to waiting
    services.approve_activity_certificates(director, new_activity)
    services.use_default_text(member, new_activity)
    new_activity.refresh_from_db()
    assert not services.is_approved(new_activity)


# --- The pages ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role_name", CREATORS)
def test_the_tab_is_read_only_and_its_urls_refuse(new_activity, design, other, make_member, role_name) -> None:
    member = make_member(role_name)
    client = client_for(member)
    html = client.get(pages.tab_url(new_activity.pk)).content.decode()
    assert ONLY in html and 'name="signatory"' not in html and "Engr. Dee Rector" in html
    assert "Prepare the certificates now" in html  # preparing stays
    before = slots(new_activity)
    url = reverse("activities:certificates_signatory", args=[new_activity.pk, "right"])
    assert client.post(url, {"signatory": other.pk, "title": "Dean"}).status_code == 403
    current = reverse("activities:certificates_signatory_current", args=[new_activity.pk, "left"])
    assert client.post(current).status_code == 403
    assert slots(new_activity) == before


def test_the_director_sees_the_choice_on_the_tab(new_activity, design, director) -> None:
    html = client_for(director).get(pages.tab_url(new_activity.pk)).content.decode()
    assert 'name="signatory"' in html and ONLY not in html


@pytest.mark.parametrize("role_name", CREATORS)
def test_the_edit_form_shows_them_read_only_and_ignores_a_posted_choice(new_activity, design, director, other,
                                                                        make_member, role_name) -> None:
    services.set_activity_signatory(director, new_activity, "left", design[2], title="Director, CSU-TBI")
    member = make_member(role_name)
    client = client_for(member)
    edit = reverse("activities:edit", args=[new_activity.pk])
    html = client.get(edit).content.decode()
    assert ONLY in html and 'name="signatory_left"' not in html and "Director, CSU-TBI" in html
    before = slots(new_activity)
    response = client.post(edit, form_post(lambda **kw: _values(new_activity, **kw), title="Renamed",
                                           signatory_right=other.pk, title_left="Hacked"))
    assert response.status_code == 302
    new_activity.refresh_from_db()
    assert new_activity.title == "Renamed" and slots(new_activity) == before


def test_the_edit_form_keeps_a_saved_title_when_the_director_saves(new_activity, design, director) -> None:
    """The title field starts with the saved "Title on this certificate" (an untouched field no longer clears it)."""
    services.set_activity_signatory(director, new_activity, "left", design[2], title="Director, CSU-TBI")
    services.prepare_activity_certificates(director, new_activity)
    services.approve_activity_certificates(director, new_activity)
    client = client_for(director)
    edit = reverse("activities:edit", args=[new_activity.pk])
    html = client.get(edit).content.decode()
    assert 'name="title_left"' in html and 'value="Director, CSU-TBI"' in html
    data = form_post(lambda **kw: _values(new_activity, **kw), max_slots=99, signatory_left=design[2].pk,
                     signatory_right=design[1].pk, title_left="Director, CSU-TBI", title_right="")
    assert client.post(edit, data).status_code == 302
    new_activity.refresh_from_db()
    assert slots(new_activity)["left"] == (design[2].pk, "Director, CSU-TBI") and services.is_approved(new_activity)


@pytest.mark.parametrize("role_name", CREATORS)
def test_the_new_form_prepares_with_the_defaults(design, other, make_member, valid_data, role_name) -> None:
    _, vp, head = design
    member = make_member(role_name)
    client = client_for(member)
    html = client.get(reverse("activities:create")).content.decode()
    assert ONLY in html and 'name="signatory_left"' not in html and "Engr. Dee Rector" in html
    data = form_post(valid_data, title="Prepared by staff", prepare_certificates="on", signatory_right=other.pk,
                     title_left="Hacked")
    assert client.post(reverse("activities:create"), data).status_code == 302
    activity = activity_services.Activity.objects.get(title="Prepared by staff")
    assert slots(activity) == {"left": (head.pk, None), "right": (vp.pk, None)} and services.is_prepared(activity)


def test_the_new_form_lets_the_director_choose(design, other, director, valid_data) -> None:
    client = client_for(director)
    assert 'name="signatory_left"' in client.get(reverse("activities:create")).content.decode()
    data = form_post(valid_data, title="Chosen", prepare_certificates="on", signatory_left=design[2].pk,
                     signatory_right=other.pk, title_right="Dean, CSU")
    assert client.post(reverse("activities:create"), data).status_code == 302
    activity = activity_services.Activity.objects.get(title="Chosen")
    assert slots(activity)["right"] == (other.pk, "Dean, CSU")


@pytest.mark.parametrize("role_name", CREATORS)
def test_the_sample_preview_uses_the_defaults(design, other, make_member, role_name, monkeypatch) -> None:
    seen = {}
    real = services._render_sample

    def spy(activity, certificate_type, fields, *args, **kwargs):
        seen.update(fields)
        return real(activity, certificate_type, fields, *args, **kwargs)

    monkeypatch.setattr(services, "_render_sample", spy)
    services.draft_sample_preview_pdf(make_member(role_name), P, details={}, slots={"left": (other, "Boss"),
                                                                                    "right": (None, "")})
    assert seen["left_signatory"] == design[2] and seen["right_signatory"] == design[1]
    assert seen["left_signatory_title"] == "Director"


def _values(activity, **overrides) -> dict:
    """The activity's current values in the shape of valid_data (for form_post on the edit form)."""
    values = activity_services.activity_values(activity)
    values["participant_categories"] = list(values["participant_categories"])
    values.update(overrides)
    return values
