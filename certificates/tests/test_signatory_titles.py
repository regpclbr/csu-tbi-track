"""Who signs and what they sign as (ASSUMPTIONS 365, 366): new activities start with the defaults, staff change a slot
or the "Title on this certificate" on the activity form and on the Certificates tab, the title prints instead of the
position, Issued certificates keep the title they were printed with, empty slots follow the defaults, permissions."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from activities import attention
from activities import services as activity_services
from activities.models import ActivityAuditLog
from certificates import services
from certificates.models import ActivitySignatory, Certificate, CertificateSignatory
from certificates.pages import tab_url

from .conftest import S, appreciation_of, certificate_of, upload
from conftest import image_bytes
from conftest import VIEW_ONLY_ROLE, creation_document_files

pytestmark = pytest.mark.django_db


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def png():
    return upload("sig.png", image_bytes(40, 20))


def choose_url(activity, slot: str) -> str:
    return reverse("activities:certificates_signatory", args=[activity.pk, slot])


def form_post(valid_data, **overrides) -> dict:
    """The create / edit form's POST data, as the browser sends it."""
    signatories = {key: overrides.pop(key) for key in list(overrides) if key.startswith(("signatory_", "title_"))}
    values = valid_data(**overrides)
    data = {
        "title": values["title"], "description": values["description"], "activity_type": values["activity_type"].pk,
        "participant_categories": [c.pk for c in values["participant_categories"]],
        "start_date": values["start_date"].isoformat(), "end_date": values["end_date"].isoformat(),
        "start_time": values["start_time"].strftime("%H:%M"), "end_time": values["end_time"].strftime("%H:%M"),
        "venue": values["venue"], "max_slots": values["max_slots"],
        "registration_deadline": timezone.localtime(values["registration_deadline"]).strftime("%Y-%m-%dT%H:%M"),
        "evaluation_deadline": timezone.localtime(values["evaluation_deadline"]).strftime("%Y-%m-%dT%H:%M"),
        "budget_amount": str(values["budget_amount"]),
        "create_as": "Draft",  # the New form's "Create as" (ASSUMPTIONS 417); the edit form ignores it
    }
    data.update(signatories)
    return data


@pytest.fixture
def president(director) -> CertificateSignatory:
    return services.add_signatory(director, "Dr. Alexander Bennett", "University President", png())


def history(activity, label: str) -> list[tuple[str, str]]:
    return list(ActivityAuditLog.objects.filter(activity=activity, field_changed=label).order_by("log_id")
                .values_list("old_value", "new_value"))


# --- A new activity starts with the defaults ------------------------------------------------------------------------


def test_a_new_activity_starts_with_the_defaults(design, director, valid_data) -> None:
    _, vp, head = design
    activity = activity_services.create_activity(director, valid_data())
    assert services.activity_signatories(activity) == {"left": head, "right": vp}
    rows = services.activity_slots(activity)
    assert rows["left"].position_on_certificate is None and services.printed_title(rows["left"]) == "Director"


def test_the_new_activity_form_is_prefilled_with_the_defaults_and_saves_them(design, director, valid_data) -> None:
    _, vp, head = design
    client = client_for(director)
    html = client.get(reverse("activities:create")).content.decode()
    assert "Who signs the certificates" in html
    assert f'<option value="{head.pk}" data-position="Director" selected>' in html
    assert f'<option value="{vp.pk}" data-position="Vice President" selected>' in html
    assert "Add a person…" in html  # the Director approves certificates
    response = client.post(reverse("activities:create"), {**form_post(
        valid_data, title="Defaults kept", signatory_left=head.pk, signatory_right=vp.pk, title_left="", title_right=""),
        **creation_document_files()})
    assert response.status_code == 302
    activity = activity_services.Activity.objects.get(title="Defaults kept")
    assert services.activity_signatories(activity) == {"left": head, "right": vp}


def test_the_new_activity_form_saves_another_person_and_a_title(design, director, president, valid_data) -> None:
    _, vp, head = design
    response = client_for(director).post(reverse("activities:create"), {**form_post(
        valid_data, title="Changed", signatory_left=head.pk, signatory_right=president.pk, title_left="Director, CSU-TBI"),
        "prepare_certificates": "on", **creation_document_files()})  # chosen only inside the optional section (420)
    assert response.status_code == 302
    activity = activity_services.Activity.objects.get(title="Changed")
    rows = services.activity_slots(activity)
    assert (rows["left"].signatory, rows["right"].signatory) == (head, president)
    assert services.printed_title(rows["left"]) == "Director, CSU-TBI"
    assert services.printed_title(rows["right"]) == "University President"


def test_the_same_person_on_both_sides_is_refused_under_the_field(design, director, valid_data) -> None:
    _, vp, head = design
    html = client_for(director).post(reverse("activities:create"), {**form_post(
        valid_data, title="Twice", signatory_left=head.pk, signatory_right=head.pk),
        "prepare_certificates": "on"}).content.decode()
    assert "The same person cannot sign on both sides." in html
    assert not activity_services.Activity.objects.filter(title="Twice").exists()  # all or nothing


def test_without_defaults_the_slots_stay_empty_and_needs_attention_says_so(director, valid_data) -> None:
    activity = activity_services.create_activity(director, valid_data())
    assert services.activity_signatories(activity) == {"left": None, "right": None}
    texts = [item.text for item in attention.activity_attention(activity)]
    assert "Choose who signs the certificates (left and right)." in texts
    item = next(i for i in attention.activity_attention(activity) if i.text.startswith("Choose who signs"))
    assert not item.informational and item.tab == "certificates"  # a normal warning
    html = client_for(director).get(reverse("activities:create")).content.decode()
    assert "There are no default signatories yet." in html and "Not chosen yet" in html


def test_certificates_are_not_created_until_both_slots_are_filled(conducted, design, director) -> None:
    activity, _ = conducted
    _, vp, head = design
    ActivitySignatory.objects.filter(activity=activity, slot="right").delete()
    assert services.auto_generate_certificates(activity) == 0
    assert "Choose who signs the certificates (right)." in [i.text for i in attention.activity_attention(activity)]
    services.set_activity_signatory(director, activity, "right", vp)
    assert services.auto_generate_certificates(activity) == 3


# --- Empty slots follow the defaults (owner decision 5) ---------------------------------------------------------


def test_setting_defaults_fills_only_empty_slots_of_activities_without_certificates(director, valid_data, conducted, design) -> None:
    activity_with_certificates, _ = conducted
    _, vp, head = design
    services.generate_missing_certificates(activity_with_certificates)
    CertificateSignatory.objects.update(default_slot=None)
    empty = activity_services.create_activity(director, valid_data(title="Empty"))
    chosen = activity_services.create_activity(director, valid_data(title="Chosen"))
    other = services.add_signatory(director, "Someone Else", "Dean", png())
    services.set_activity_signatory(director, chosen, "left", other)
    ActivitySignatory.objects.filter(activity=activity_with_certificates, slot="right").delete()  # has certificates

    result = services.set_default_signatories(director, head, vp)

    assert result == {"filled": 3}  # Empty: left + right; Chosen: right only
    assert services.activity_signatories(empty) == {"left": head, "right": vp}
    assert services.activity_signatories(chosen) == {"left": other, "right": vp}  # the chosen slot is never replaced
    assert services.activity_signatories(activity_with_certificates)["right"] is None  # never touched
    rows = ActivityAuditLog.objects.filter(activity=empty, field_changed="Certificate signatory (left)")
    assert [(r.changed_by_id, r.old_value, r.new_value) for r in rows] == [(None, "", "Engr. Dee Rector (Director), default")]


# --- Changing a slot or the title on the Certificates tab ----------------------------------------------------------


def test_the_title_prints_instead_of_the_position(generated, director, fake_pdf_renderer) -> None:
    activity, people = generated
    services.set_activity_signatory(director, activity, "left", services.activity_signatories(activity)["left"],
                                    title="Director, CSU-TBI")
    certificate = certificate_of(people[0])
    assert certificate.left_signatory_title == "Director, CSU-TBI"
    assert certificate.right_signatory_title == "Vice President"
    services.preview_pdf(director, certificate)
    text = fake_pdf_renderer.last_text()
    assert "Director, CSU-TBI" in text and "Vice President" in text


def test_changing_the_title_on_the_tab_follows_the_refresh_rules(generated, director, run_tasks,
                                                                  django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_activity_certificates(director, activity)  # Approved hard copy, PDF rendered
    speaker = appreciation_of(activity)
    assert speaker.status == S.APPROVED and speaker.file_path
    issued = certificate_of(people[0])
    Certificate.objects.filter(pk=issued.pk).update(status=S.ISSUED, issued_at=timezone.now())
    head = services.activity_signatories(activity)["left"]

    response = client_for(director).post(choose_url(activity, "left"), {"signatory": head.pk, "title": "TBI Director"})

    assert response.status_code == 302
    assert history(activity, "Certificate signatory (left)")[-1] == ("Engr. Dee Rector (Director)",
                                                                    "Engr. Dee Rector (TBI Director)")
    waiting = certificate_of(people[1])
    assert waiting.left_signatory_title == "TBI Director" and waiting.status == S.GENERATED  # not issued: re-rendered
    speaker.refresh_from_db()
    assert speaker.status == S.GENERATED and not speaker.file_path  # Approved -> waiting for approval again
    issued.refresh_from_db()
    assert issued.left_signatory_title == "Director"  # Issued keeps the title it was printed with
    assert services.outdated_ids(activity) == {issued.pk}  # "Issued with an old signature", can be reissued


def test_issued_certificates_keep_their_printed_title_and_reissue_uses_the_new_one(generated, director, run_tasks,
                                                                                   django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    issued = certificate_of(people[0])
    Certificate.objects.filter(pk=issued.pk).update(status=S.ISSUED, issued_at=timezone.now())
    assert services.outdated_ids(activity) == set()
    head = services.activity_signatories(activity)["left"]
    services.set_activity_signatory(director, activity, "left", head, title="Director, CSU-TBI")
    issued.refresh_from_db()
    assert issued.left_signatory_title == "Director" and services.outdated_ids(activity) == {issued.pk}
    # Clearing the title again makes it current again (the position prints).
    services.set_activity_signatory(director, activity, "left", head, title="")
    assert services.outdated_ids(activity) == set()
    services.set_activity_signatory(director, activity, "left", head, title="Director, CSU-TBI")
    with django_capture_on_commit_callbacks(execute=True):
        services.reissue_certificate(director, issued)
    issued.refresh_from_db()
    assert issued.left_signatory_title == "Director, CSU-TBI" and services.outdated_ids(activity) == set()


def test_a_new_person_clears_the_title_unless_one_is_given(generated, director, president) -> None:
    activity, _ = generated
    services.set_activity_signatory(director, activity, "right", services.activity_signatories(activity)["right"],
                                    title="VP for Research")
    services.set_activity_signatory(director, activity, "right", president)
    assert services.activity_slots(activity)["right"].position_on_certificate is None
    services.set_activity_signatory(director, activity, "right", president, title="  ")
    assert services.activity_slots(activity)["right"].position_on_certificate is None  # spaces = the position


def test_a_title_longer_than_255_characters_is_refused(generated, director) -> None:
    activity, _ = generated
    with pytest.raises(services.CertificateError, match="at most 255"):
        services.set_activity_signatory(director, activity, "left", services.activity_signatories(activity)["left"],
                                        title="x" * 256)


def test_the_tab_shows_the_title_field_and_add_a_person(generated, director) -> None:
    activity, _ = generated
    head = services.activity_signatories(activity)["left"]
    services.set_activity_signatory(director, activity, "left", head, title="Director, CSU-TBI")
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "Title on this certificate" in html and 'value="Director, CSU-TBI"' in html
    assert '<option value="add">Add a person…</option>' in html
    assert "usual position: Director" in html


# --- Changing a slot or the title on the activity edit form ----------------------------------------------------------


def test_changing_a_slot_and_a_title_on_the_edit_form(design, director, president, valid_data) -> None:
    _, vp, head = design
    activity = activity_services.create_activity(director, valid_data(title="Edit me"))
    response = client_for(director).post(reverse("activities:edit", args=[activity.pk]), form_post(
        valid_data, title="Edit me", signatory_left=head.pk, title_left="Director, CSU-TBI",
        signatory_right=president.pk, title_right=""))
    assert response.status_code == 302
    rows = services.activity_slots(activity)
    assert (rows["left"].signatory, services.printed_title(rows["left"])) == (head, "Director, CSU-TBI")
    assert rows["right"].signatory == president
    assert history(activity, "Certificate signatory (right)") == [
        ("Dr. Vice Pres (Vice President)", "Dr. Alexander Bennett (University President)")]


def test_swapping_left_and_right_on_the_edit_form(design, director, valid_data) -> None:
    _, vp, head = design
    activity = activity_services.create_activity(director, valid_data(title="Swap"))
    response = client_for(director).post(reverse("activities:edit", args=[activity.pk]), form_post(
        valid_data, title="Swap", signatory_left=vp.pk, signatory_right=head.pk))
    assert response.status_code == 302
    assert services.activity_signatories(activity) == {"left": vp, "right": head}


def test_the_edit_form_never_empties_a_slot(design, director, valid_data) -> None:
    _, vp, head = design
    activity = activity_services.create_activity(director, valid_data(title="Keep"))
    html = client_for(director).get(reverse("activities:edit", args=[activity.pk])).content.decode()
    section = html.split('id="section-signatories"')[1]
    assert "Not chosen yet" not in section  # no empty option once chosen
    client_for(director).post(reverse("activities:edit", args=[activity.pk]), form_post(valid_data, title="Keep"))
    assert services.activity_signatories(activity) == {"left": head, "right": vp}  # nothing sent = no change


# --- Permissions --------------------------------------------------------------------------------------------------


def test_view_only_members_only_views_slots_and_titles(generated, view_only, design) -> None:
    activity, _ = generated
    _, vp, head = design
    with pytest.raises(PermissionDenied):
        services.set_activity_signatory(view_only, activity, "left", head, title="Anything")
    with pytest.raises(PermissionDenied):
        services.use_current_version(view_only, activity, "left")
    client = client_for(view_only)
    assert client.post(choose_url(activity, "left"), {"signatory": head.pk, "title": "X"}).status_code == 403
    assert client.get(reverse("activities:create")).status_code == 403
    html = client.get(tab_url(activity.pk)).content.decode()
    signatures = html.split('id="signatures-title"')[1].split("certificate-preview")[0]
    assert "Title on this certificate</label>" not in signatures and 'name="title"' not in signatures
    assert services.activity_slots(activity)["left"].position_on_certificate is None


def test_a_creator_without_the_approval_flag_changes_slots_but_cannot_add_people(generated, make_member, roles, design) -> None:
    activity, _ = generated
    _, vp, head = design
    roles[VIEW_ONLY_ROLE].can_create_activity = True  # a role that creates activities but does not approve certificates
    roles[VIEW_ONLY_ROLE].save(update_fields=["can_create_activity"])
    creator = make_member(VIEW_ONLY_ROLE, email="creator@example.com")
    services.set_activity_signatory(creator, activity, "left", head, title="Director, CSU-TBI")
    assert services.printed_title(services.activity_slots(activity)["left"]) == "Director, CSU-TBI"
    html = client_for(creator).get(reverse("activities:create")).content.decode()
    assert "Who signs the certificates" in html and "Add a person…" not in html
    assert client_for(creator).post(reverse("certificates:signatory_add_inline"), {"name": "A", "position": "B"}).status_code == 403


def test_the_system_administrator_no_longer_changes_slots(generated, make_member, design) -> None:
    """ASSUMPTIONS 421: the System Administrator neither creates activities nor approves certificates by default."""
    activity, _ = generated
    _, vp, head = design
    sysadmin = make_member(Role.SYSTEM_ADMINISTRATOR, email="sys@example.com")
    assert client_for(sysadmin).post(choose_url(activity, "left"), {"signatory": head.pk, "title": "TBI Head"}).status_code == 403
    assert services.printed_title(services.activity_slots(activity)["left"]) == "Director"
    assert client_for(sysadmin).get(reverse("activities:create")).status_code == 403


def test_set_activity_signatories_refuses_an_inactive_new_person(design, director, president, valid_data) -> None:
    activity = activity_services.create_activity(director, valid_data())
    services.deactivate_signatory(director, president)
    president.refresh_from_db()
    with pytest.raises(ValidationError) as error:
        services.set_activity_signatories(director, activity, {"right": (president, "")})
    assert "signatory_right" in error.value.message_dict
