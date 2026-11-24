"""Prepare and approve certificates before Conducted (ASSUMPTIONS 420, NAVIGATU UAT): the New activity form's optional
"Prepare the certificates now" (closed by default; not chosen = the defaults and today's flow), the sample preview with
a sample name, the reviewers told once, early approval from the Certificates tab, certificates created already approved
at Conducted and emailed after each evaluation, the cancel rules before Conducted and the approval card states."""

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from activities import services as activity_services
from activities.models import Activity, ActivityAuditLog
from certificates import pages, services
from certificates.models import Certificate, CertificateSignatory, CertificateText
from notifications.models import Notification
from notifications.services import certificate_reviewers

from .conftest import S, appreciation_of, certificate_of, template_upload, upload
from .test_signatory_titles import form_post
from conftest import image_bytes

pytestmark = pytest.mark.django_db

P, A = CertificateText.CertificateType.PARTICIPATION, CertificateText.CertificateType.APPRECIATION


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def fresh(activity) -> Activity:
    return Activity.objects.select_related("status", "certificates_prepared_by").get(pk=activity.pk)


def prepared_notifications(activity=None):
    rows = Notification.objects.filter(type=Notification.CERTIFICATES_PREPARED)
    return rows.filter(activity=activity) if activity is not None else rows


def text_post(participation_title: str = "Certificate of Attendance") -> dict:
    """Both types' wording as the New form sends it (both are on the page, pre-filled with the default)."""
    data = {}
    for kind in (P, A):
        values = services.text_values(services.current_texts(None)[kind])
        if kind == P:
            values["title"] = participation_title
        data.update({f"{kind}-{field}": value for field, value in values.items()})
    return data


@pytest.fixture
def staff(make_member):
    return make_member("Admin Staff")


@pytest.fixture
def president(director) -> CertificateSignatory:
    return services.add_signatory(director, "Dr. Alexander Bennett", "University President", upload("p.png", image_bytes()))


@pytest.fixture
def prepared(new_activity, design, staff):
    """A Draft activity whose certificates Admin Staff prepared on the tab."""
    services.prepare_activity_certificates(staff, new_activity)
    return fresh(new_activity)


@pytest.fixture
def early(prepared, director):
    """Prepared and approved by the Director before Conducted."""
    services.approve_activity_certificates(director, prepared)
    return fresh(prepared)


def state_of(activity):
    activity = fresh(activity)
    return services.approval_state(activity, services.approval_counts([activity.pk])[activity.pk])


# --- The New activity form -------------------------------------------------------------------------------------------


def test_the_section_is_closed_by_default(design, director) -> None:
    html = client_for(director).get(reverse("activities:create")).content.decode()
    assert "Prepare the certificates now (optional)" in html
    box = html.split('name="prepare_certificates"', 1)[1].split(">", 1)[0]
    assert "checked" not in box  # not ticked
    inner = html.split('x-show="prepare"', 1)[1].split(">", 1)[0]
    assert 'style="display: none"' in inner  # the signatories, text and preview start hidden
    hidden = html.split('x-show="prepare"', 1)[1]
    assert "Who signs the certificates" in hidden  # only inside the optional section


def test_the_edit_form_keeps_who_signs(new_activity, design, director) -> None:
    html = client_for(director).get(reverse("activities:edit", args=[new_activity.pk])).content.decode()
    assert "Who signs the certificates" in html and "Prepare the certificates now" not in html


def test_not_chosen_uses_the_defaults_and_ignores_the_fields(design, director, staff, president, valid_data) -> None:
    _, vp, head = design
    response = client_for(staff).post(reverse("activities:create"), {
        **form_post(valid_data, title="Not prepared", signatory_left=president.pk, signatory_right=vp.pk,
                    title_left="Ignored"),
        "certificate_text_mode": "custom", **text_post("Ignored title")})
    assert response.status_code == 302
    activity = fresh(Activity.objects.get(title="Not prepared"))
    assert services.activity_signatories(activity) == {"left": head, "right": vp}
    assert services.activity_slots(activity)["left"].position_on_certificate is None
    assert not services.uses_custom_text(activity)
    assert not services.is_prepared(activity) and not prepared_notifications().exists()
    assert state_of(activity).kind == services.NOT_PREPARED_KIND


def test_not_chosen_ignores_an_invalid_signatory_choice(design, director, valid_data) -> None:
    response = client_for(director).post(reverse("activities:create"), form_post(
        valid_data, title="Ignored add", signatory_left="add"))
    assert response.status_code == 302
    assert Activity.objects.filter(title="Ignored add").exists()


def test_chosen_saves_signatories_text_and_tells_the_director_once(design, director, staff, president,
                                                                   valid_data) -> None:
    _, vp, head = design
    response = client_for(staff).post(reverse("activities:create"), {
        **form_post(valid_data, title="Prepared now", signatory_left=president.pk, signatory_right=vp.pk,
                    title_left="President, CSU"),
        "prepare_certificates": "on", "certificate_text_mode": "custom", **text_post()})
    assert response.status_code == 302
    activity = fresh(Activity.objects.get(title="Prepared now"))
    # ASSUMPTIONS 431: staff cannot choose who signs; the posted choice is ignored and the defaults are used.
    assert services.activity_signatories(activity) == {"left": head, "right": vp}
    assert services.printed_title(services.activity_slots(activity)["left"]) == "Director"
    assert services.current_texts(activity)[P].title == "Certificate of Attendance"
    assert services.is_prepared(activity) and activity.certificates_prepared_by == staff
    rows = list(prepared_notifications(activity).values_list("member", "message"))
    assert rows == [(director.pk, "Certificates of Prepared now are ready for your review")]
    assert ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificates",
                                           new_value=f"Prepared for review by {staff.name}").count() == 1
    # Once only: preparing again is refused and tells nobody.
    with pytest.raises(services.CertificateError, match="already prepared"):
        services.prepare_activity_certificates(staff, activity)
    services.set_activity_signatory(director, activity, "left", president)  # a later change tells nobody either
    assert prepared_notifications(activity).count() == 1


def test_chosen_with_the_default_text_keeps_the_default(design, staff, valid_data) -> None:
    _, vp, head = design
    client_for(staff).post(reverse("activities:create"), {
        **form_post(valid_data, title="Default text", signatory_left=head.pk, signatory_right=vp.pk),
        "prepare_certificates": "on", "certificate_text_mode": "default", **text_post("Not used")})
    activity = fresh(Activity.objects.get(title="Default text"))
    assert services.is_prepared(activity) and not services.uses_custom_text(activity)


def test_refused_text_is_shown_under_the_field_and_nothing_is_saved(design, staff, valid_data) -> None:
    _, vp, head = design
    data = {**form_post(valid_data, title="Bad text", signatory_left=head.pk, signatory_right=vp.pk),
            "prepare_certificates": "on", "certificate_text_mode": "custom", **text_post()}
    data[f"{P}-main_sentence"] = "Hello {nonsense}"
    html = client_for(staff).post(reverse("activities:create"), data).content.decode()
    assert "Please correct the certificate text marked below." in html
    assert 'id="participation-main_sentence-error"' in html
    assert not Activity.objects.filter(title="Bad text").exists() and not prepared_notifications().exists()
    box = html.split('name="prepare_certificates"', 1)[1].split(">", 1)[0]
    assert "checked" in box  # kept ticked


def test_the_director_preparing_tells_nobody_and_only_reviewers_are_told(design, director, make_member,
                                                                         new_activity) -> None:
    services.prepare_activity_certificates(director, new_activity)
    assert not prepared_notifications().exists()  # never the member who prepared them
    head = make_member("Incubation Project Head")  # approves certificates, does not manage signatories
    assert head not in certificate_reviewers() and director in certificate_reviewers()


def test_notification_opens_the_certificates_tab(prepared, director) -> None:
    from notifications.pages import target_label, target_url

    note = prepared_notifications(prepared).get()
    assert target_url(note).endswith(f"/activities/{prepared.pk}/?tab=certificates")
    assert target_label(note) == "Open certificates"


# --- The sample preview --------------------------------------------------------------------------------------------


def test_the_tab_preview_uses_a_sample_name_and_the_active_design(prepared, director, fake_pdf_renderer) -> None:
    response = client_for(director).get(reverse("activities:certificates_sample_preview", args=[prepared.pk]))
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert response["X-Frame-Options"] == "SAMEORIGIN" and "no-store" in response["Cache-Control"]
    text = fake_pdf_renderer.last_text()
    assert "Juan Dela Cruz" in text and prepared.title in text and "PREVIEW" in text and "SAMPLE" in text
    assert "Engr. Dee Rector" in text and "Dr. Vice Pres" in text
    assert "data:image/png;base64" in fake_pdf_renderer.last  # the active design's background
    assert not Certificate.objects.exists()  # nothing stored


def test_the_appreciation_sample_uses_the_resource_person(prepared, director, fake_pdf_renderer) -> None:
    speaker = activity_services.create_resource_person(director, "Dr. Lea Gomez")
    activity_services.add_resource_person(director, prepared, speaker, topic="Pricing")
    client_for(director).get(reverse("activities:certificates_sample_preview", args=[prepared.pk]) + f"?type={A}")
    assert "Dr. Lea Gomez" in fake_pdf_renderer.last_text()


def test_the_new_form_preview_uses_what_was_typed(design, staff, president, fake_pdf_renderer) -> None:
    _, vp, _ = design
    query = {"type": P, "title": "Typed Title", "start_date": "2026-12-10", "end_date": "2026-12-11", "venue": "Hall B",
             "signatory_left": president.pk, "signatory_right": vp.pk, "title_left": "President, CSU",
             "certificate_text_mode": "custom", **{key: value for key, value in text_post().items()
                                                   if key.startswith(P)}}
    response = client_for(staff).get(reverse("certificates:draft_sample_preview"), query)
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    text = fake_pdf_renderer.last_text()
    assert "Juan Dela Cruz" in text and "Typed Title" in text and "Certificate of Attendance" in text
    # ASSUMPTIONS 431: staff get the defaults, so the sample shows them, never the typed choice.
    assert "President, CSU" not in text and not Activity.objects.filter(title="Typed Title").exists()


def test_the_preview_says_why_when_it_cannot_be_shown(new_activity, director) -> None:
    # No design and no signatories yet.
    html = client_for(director).get(reverse("activities:certificates_sample_preview", args=[new_activity.pk]))
    assert "The preview is not available yet." in html.content.decode()
    html = client_for(director).get(reverse("certificates:draft_sample_preview"), {"type": P}).content.decode()
    assert "no active certificate design" in html


def test_the_new_form_preview_needs_create_activity(design, view_only) -> None:
    assert client_for(view_only).get(reverse("certificates:draft_sample_preview")).status_code == 403


# --- Early approval ------------------------------------------------------------------------------------------------


def test_approval_before_conducted_needs_preparing_first(new_activity, design, director) -> None:
    assert services.approval_closed_reason(fresh(new_activity)) == services.NOT_PREPARED
    with pytest.raises(services.CertificateError, match="Prepare the certificates first"):
        services.approve_activity_certificates(director, new_activity)
    client = client_for(director)
    client.post(reverse("activities:certificates_approve_activity", args=[new_activity.pk]))
    assert not services.is_approved(fresh(new_activity))


def test_a_prepared_activity_is_approved_before_conducted(prepared, director) -> None:
    client = client_for(director)
    html = client.get(pages.tab_url(prepared.pk)).content.decode()
    assert services.PREPARED_TEXT in html and "Approve certificates for this activity" in html
    assert "Certificate preview" in html and "Prepare the certificates now" not in html
    response = client.post(reverse("activities:certificates_approve_activity", args=[prepared.pk]), follow=True)
    assert "They will be created already approved when the activity is Conducted" in response.content.decode()
    activity = fresh(prepared)
    assert services.is_approved(activity) and activity.certificates_approved_by == director
    state = state_of(activity)
    assert state.kind == services.APPROVED_KIND and state.note == services.EARLY_APPROVED_NOTE
    assert ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificate approval",
                                           new_value=f"Approved by {director.name} (before Conducted)").exists()


def test_early_approval_still_needs_both_signatures(new_activity, design, director, staff) -> None:
    _, vp, _ = design
    CertificateSignatory.objects.filter(pk=vp.pk).update(signature_image_path="")
    services.prepare_activity_certificates(staff, new_activity)
    assert "Add the signature of Dr. Vice Pres" in services.approval_closed_reason(fresh(new_activity))


def test_a_prepared_activity_is_not_counted_as_needing_approval(prepared) -> None:
    assert not Activity.objects.filter(pk=prepared.pk, certificates__in=Certificate.objects.filter(
        services.needs_approval_q())).exists()
    assert state_of(prepared).kind == services.PREPARED_KIND


def test_certificates_are_created_approved_and_emailed_after_each_evaluation(
        attended, design, director, staff, evaluate, run_tasks, django_capture_on_commit_callbacks) -> None:
    people = attended(2)  # Ongoing, both attended
    activity = people[0].activity
    speaker = activity_services.create_resource_person(director, "Dr. Maria Santos", "maria@example.com")
    activity_services.add_resource_person(director, activity, speaker, topic="Pitch Decks")
    services.prepare_activity_certificates(staff, activity)
    services.approve_activity_certificates(director, activity)
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0], name="Ana Cruz", email="ana.first@example.com")  # while Ongoing (item 419's links)
    assert not Certificate.objects.exists() and mail.outbox == []
    with django_capture_on_commit_callbacks(execute=True):
        activity_services.change_status(director, activity, "Conducted")
    first = certificate_of(people[0])
    assert first.status == S.ISSUED and first.approved_by == director and first.sent_at
    assert [m.to for m in mail.outbox] == [["ana.first@example.com"]]
    assert appreciation_of(activity).status == S.APPROVED  # ready to print at once
    assert certificate_of(people[1]).status == S.GENERATED  # waits for the evaluation, no approval click needed
    assert state_of(activity).kind == services.APPROVED_KIND and state_of(activity).note == ""  # item 373 card
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[1], name="Ben Reyes", email="ben.second@example.com")
    assert certificate_of(people[1]).status == S.ISSUED
    assert [m.to for m in mail.outbox] == [["ana.first@example.com"], ["ben.second@example.com"]]


def test_not_prepared_is_todays_flow(attended, design, director, evaluate, django_capture_on_commit_callbacks) -> None:
    people = attended(1)
    activity = people[0].activity
    assert state_of(activity).kind == services.NOT_PREPARED_KIND and not state_of(activity).offers_approval
    activity_services.change_status(director, activity, "Conducted")
    assert certificate_of(people[0]).status == S.GENERATED
    assert state_of(activity).kind == services.NEEDS_APPROVAL_KIND  # item 373, unchanged
    services.approve_activity_certificates(director, activity)
    assert services.is_approved(fresh(activity))


def test_prepared_but_not_approved_waits_for_approval_at_conducted(attended, design, director, staff) -> None:
    people = attended(1)
    activity = people[0].activity
    services.prepare_activity_certificates(staff, activity)
    activity_services.change_status(director, activity, "Conducted")
    assert certificate_of(people[0]).status == S.GENERATED
    assert state_of(activity).kind == services.NEEDS_APPROVAL_KIND
    with pytest.raises(services.CertificateError, match="Conducted"):
        services.prepare_activity_certificates(staff, activity)


# --- Cancel rules before Conducted ---------------------------------------------------------------------------------


def _signatory(director, early, design):
    other = services.add_signatory(director, "Dr. Other", "Chancellor", upload("o.png", image_bytes()))
    services.set_activity_signatory(director, early, "right", other)


def _title(director, early, design):
    services.set_activity_signatory(director, early, "right", design[1], title="Vice President, CSU")


def _text(director, early, design):
    values = services.text_values(services.current_texts(early)[P])
    services.save_activity_text(director, early, {P: {**values, "title": "Certificate of Attendance"}})


def _default_text(director, early, design):
    values = services.text_values(services.current_texts(None)[P])
    services.save_default_text(director, P, {**values, "title": "Certificate of Attendance"})


def _new_version(director, early, design):
    services.replace_signature(director, design[1], upload("new.png", image_bytes()))


def _design(director, early, design):
    admin = Role.objects.get(role_name=Role.SYSTEM_ADMINISTRATOR)
    from accounts.models import TBIMember

    member = TBIMember.objects.create_user("sysadmin@example.com", "Sys Admin", admin, "Strong-Pass-2026!")
    services.upload_template(member, "New design", template_upload("n.png"), activate=True)


def _activity_title(director, early, design):
    activity_services.update_activity(director, early, {"title": "Renamed"})


def _venue(director, early, design):
    activity_services.update_activity(director, early, {"venue": "Another hall"})


def _resource_person(director, early, design):
    person = activity_services.create_resource_person(director, "Dr. New Speaker")
    activity_services.add_resource_person(director, early, person)


@pytest.mark.parametrize("change, reason", [
    (_signatory, "right signatory changed"),
    (_title, "right title changed"),
    (_text, "certificate text changed"),
    (_default_text, "default Participation text changed"),
    (_new_version, "new version of Dr. Vice Pres"),
    (_design, "certificate design changed"),
    (_activity_title, "activity details changed"),
    (_venue, "activity details changed"),
    (_resource_person, "resource persons changed"),
])
def test_a_change_before_conducted_cancels_the_early_approval(early, director, design, change, reason) -> None:
    change(director, early, design)
    activity = fresh(early)
    assert not services.is_approved(activity)
    assert state_of(activity).kind == services.PREPARED_KIND  # still prepared: approve again
    last = ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificate approval").latest("log_id")
    assert last.new_value.startswith("Needs approval (cancelled: ") and reason in last.new_value


def test_a_change_that_is_not_printed_keeps_the_early_approval(early, director) -> None:
    activity_services.update_activity(director, early, {"max_slots": 99})
    assert services.is_approved(fresh(early))


def test_the_default_text_dialog_counts_early_approvals(early) -> None:
    impact = services.default_text_impact(P)
    assert impact["early"] == 1
    assert "approved before it was Conducted is cancelled" in services.default_text_impact_text(impact)


# --- The tab before Conducted ----------------------------------------------------------------------------------------


def test_the_tab_offers_prepare_when_not_prepared(new_activity, design, director, staff) -> None:
    client = client_for(staff)
    html = client.get(pages.tab_url(new_activity.pk)).content.decode()
    assert services.NOT_PREPARED_TEXT in html and "Prepare the certificates now" in html
    assert "Approve certificates for this activity" not in html
    assert "Certificate preview" in html  # the sample preview card
    response = client.post(reverse("activities:certificates_prepare", args=[new_activity.pk]))
    assert response.status_code == 302
    assert services.is_prepared(fresh(new_activity)) and prepared_notifications(new_activity).count() == 1
    html = client.get(pages.tab_url(new_activity.pk)).content.decode()
    assert services.PREPARED_TEXT in html and f"Prepared by {staff.name} on " in html


def test_the_tab_after_early_approval(early, director) -> None:
    html = client_for(director).get(pages.tab_url(early.pk)).content.decode()
    assert f"Approved by {director.name} on " in html and services.EARLY_APPROVED_NOTE in html
    assert "Approve certificates for this activity" not in html


def test_view_only_cannot_prepare(new_activity, design, view_only) -> None:
    client = client_for(view_only)
    html = client.get(pages.tab_url(new_activity.pk)).content.decode()
    assert services.NOT_PREPARED_TEXT in html and "Prepare the certificates now" not in html
    assert client.post(reverse("activities:certificates_prepare", args=[new_activity.pk])).status_code == 403
    assert not services.is_prepared(fresh(new_activity))


def test_add_a_person_stays_with_the_signatory_managers(design, director, staff) -> None:
    # F / item 418: choosing from the list is unchanged; only can_manage_signatories adds people.
    assert "Add a person…" in client_for(director).get(reverse("activities:create")).content.decode()
    html = client_for(staff).get(reverse("activities:create")).content.decode()
    # ASSUMPTIONS 431: staff see the signatories read only.
    assert "Add a person…" not in html and 'name="signatory_left"' not in html
    assert "Only the Director chooses who signs." in html


# --- The Certificates card follows the approval card before Conducted -----------------------------------------------


def card_html(member, activity) -> str:
    """The tab's text, unescaped (apostrophes are escaped in the HTML)."""
    from html import unescape

    return unescape(client_for(member).get(pages.tab_url(activity.pk)).content.decode())


def test_the_list_card_when_not_prepared_keeps_todays_text(new_activity, design, director) -> None:
    html = card_html(director, new_activity)
    assert "No certificates yet" in html and pages.NOT_CONDUCTED_EMPTY[1] in html
    assert pages.LIST_SUBTITLE in html and pages.PREPARED_EMPTY[1] not in html


def test_the_list_card_when_prepared(prepared, director) -> None:
    html = card_html(director, prepared)
    assert pages.PREPARED_EMPTY[0] in html and pages.PREPARED_EMPTY[1] in html
    assert "Certificates are prepared (see the sample above)." in html
    assert pages.PREPARED_SUBTITLE in html
    assert "No certificates yet" not in html and pages.LIST_SUBTITLE not in html


def test_the_list_card_when_prepared_and_approved(early, director) -> None:
    html = card_html(director, early)
    assert pages.PREPARED_APPROVED_EMPTY[1] in html and pages.PREPARED_APPROVED_SUBTITLE in html
    assert "emailed as soon as they submit their evaluation." in html
    assert "No certificates yet" not in html and pages.PREPARED_EMPTY[1] not in html


def test_the_list_card_after_conducted_keeps_todays_text(attended, design, director, staff) -> None:
    people = attended(1)
    activity = people[0].activity
    services.prepare_activity_certificates(staff, activity)
    services.approve_activity_certificates(director, activity)
    activity_services.change_status(director, activity, "Conducted")
    html = card_html(director, activity)
    assert pages.LIST_SUBTITLE in html  # item 373's states and the list itself take over
    assert pages.PREPARED_APPROVED_SUBTITLE not in html and pages.PREPARED_APPROVED_EMPTY[1] not in html
