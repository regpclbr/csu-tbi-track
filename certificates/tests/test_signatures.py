"""Signatories (ASSUMPTIONS 361, 363): who approves and manages signatures, each activity's left and right signatory,
defaults for new activities, new versions of a person (image replaced, name or position edited) and what they do to
certificates, the impact shown before saving, approval blocked until both signatures are there, the tab and the
Signatories page, the audit lines, and the Needs attention line."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from activities import attention
from activities import services as activity_services
from activities.models import ActivityAuditLog
from certificates import pdf, services
from certificates.models import ActivitySignatory, Certificate, CertificateSignatory
from certificates.pages import tab_url

from .conftest import S, appreciation_of, certificate_of, upload
from conftest import image_bytes

pytestmark = pytest.mark.django_db

APPROVERS = ["Director"]  # ASSUMPTIONS 421 (was also both Project Heads, Admin Staff and the System Administrator)
NOT_APPROVERS = ["Incubation Project Head", "Community Development Head", "Admin Staff", Role.SYSTEM_ADMINISTRATOR]


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def png(name: str = "sig.png") -> object:
    return upload(name, image_bytes(40, 20))


def jpg(name: str = "sig.jpg") -> object:
    return upload(name, image_bytes(40, 20, kind="JPEG"))


def without_image(signatory: CertificateSignatory) -> CertificateSignatory:
    CertificateSignatory.objects.filter(pk=signatory.pk).update(signature_image_path=None)
    signatory.refresh_from_db()
    return signatory


def signature_url(activity, slot: str) -> str:
    return reverse("activities:certificates_signature", args=[activity.pk, slot])


def choose_url(activity, slot: str) -> str:
    return reverse("activities:certificates_signatory", args=[activity.pk, slot])


def person(member, name: str, position: str, image: bool = True) -> CertificateSignatory:
    return services.add_signatory(member, name, position, png() if image else None)


@pytest.fixture
def make_activity(director, valid_data):
    """Factory: make_activity(title) creates another Draft activity through the services (it starts with the default
    signatories)."""

    def make(title: str):
        return activity_services.create_activity(director, valid_data(title=title))

    return make


@pytest.fixture
def conduct(director, with_forms, move_to, signup):
    """conduct(activity): one attendee, then Conducted (the System creates the certificates at that move)."""
    from registrations import services as registration_services

    def run(activity):
        with_forms(activity)
        move_to(activity, "Scheduled")
        registration = signup(activity, f"{activity.title.split()[0].lower()}@example.com")
        activity_services.change_status(director, activity, "Ongoing")
        registration_services.set_attendance(director, activity, [registration.pk], True, "email")
        activity_services.change_status(director, activity, "Conducted")
        activity.refresh_from_db()
        return activity

    return run


@pytest.fixture
def evaluated(generated, evaluate):
    """Both participants evaluated: every certificate is ready for approval (ASSUMPTIONS 362)."""
    activity, people = generated
    for n, registration in enumerate(people):
        evaluate(registration, name=f"Person {n}", email=f"person{n}.cert@example.com")
    return activity, people


@pytest.fixture
def issued(generated, evaluate, approve_all, run_tasks, director, django_capture_on_commit_callbacks):
    """people[0] evaluated and issued (emailed); the resource person approved and handed over (Issued)."""
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
        approve_all(activity)
    services.mark_handed_over(director, activity=activity)
    assert set(Certificate.objects.filter(activity=activity).values_list("status", flat=True)) == {S.ISSUED, S.GENERATED}
    return activity, people


# --- Who may approve, manage signatures and choose signatories ---------------------------------------------------


def test_the_seeded_approver_roles_have_the_flag(roles) -> None:
    assert {name for name, role in roles.items() if role.can_approve_certificates} == set(APPROVERS)


@pytest.mark.parametrize("role_name", APPROVERS)
def test_approvers_approve_all_for_the_activity(evaluated, make_member, role_name) -> None:
    activity, _ = evaluated
    member = make_member(role_name, email=f"approver.{role_name.split()[0].lower()}@example.com")
    assert len(services.approve_activity_certificates(member, activity)) == 3
    assert set(Certificate.objects.values_list("status", "approved_by")) == {(S.APPROVED, member.pk)}


@pytest.mark.parametrize("role_name", NOT_APPROVERS)
def test_other_roles_cannot_approve(evaluated, make_member, role_name) -> None:
    activity, _ = evaluated
    member = make_member(role_name, email=f"other.{role_name.split()[0].lower()}@example.com")
    with pytest.raises(PermissionDenied):
        services.approve_activity_certificates(member, activity)
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}


def test_view_only_members_only_views(generated, view_only, design) -> None:
    activity, _ = generated
    _, vp, head = design
    with pytest.raises(PermissionDenied):
        services.approve_activity_certificates(view_only, activity)
    with pytest.raises(PermissionDenied):
        services.upload_signature(view_only, activity, "right", png())
    with pytest.raises(PermissionDenied):
        services.set_activity_signatory(view_only, activity, "left", vp)
    client = client_for(view_only)
    assert client.post(reverse("activities:certificates_approve_activity", args=[activity.pk])).status_code == 403
    assert client.post(signature_url(activity, "right"), {"file": png()}).status_code == 403
    assert client.post(choose_url(activity, "left"), {"signatory": vp.pk}).status_code == 403
    assert client.get(reverse("certificates:signatories")).status_code == 403
    html = client.get(tab_url(activity.pk)).content.decode()
    signatures = html.split('id="signatures-title"')[1].split("certificate-preview")[0]
    assert "Waiting for approval" in html and "Approve certificates for this activity" not in html
    assert head.name in signatures and "Director" in signatures and vp.name in signatures
    assert 'name="file"' not in signatures and 'name="signatory"' not in signatures and "Add a person" not in signatures


def test_activity_creators_choose_signatories_but_never_upload(generated, design, make_member) -> None:
    activity, _ = generated
    _, vp, head = design
    president = person(make_member("Director", email="d2@example.com"), "Dr. Pres", "University President")
    head_of_projects = make_member("Incubation Project Head", email="iph@example.com")
    from accounts import services as account_services

    # A creator without can_approve_certificates (the role flag switched off) may still choose.
    admin = make_member(Role.SYSTEM_ADMINISTRATOR, email="sysadmin2@example.com")
    account_services.set_role_certificate_approval(admin, head_of_projects.role, False)
    head_of_projects.refresh_from_db()
    assert services.set_activity_signatory(head_of_projects, activity, "right", president)["changed"]
    with pytest.raises(PermissionDenied):
        services.upload_signature(head_of_projects, activity, "right", png())
    html = client_for(head_of_projects).get(tab_url(activity.pk)).content.decode()
    signatures = html.split('id="signatures-title"')[1].split("certificate-preview")[0]
    assert 'name="signatory"' in signatures and 'name="file"' not in signatures and "Add a person" not in signatures


def test_system_administrator_neither_adds_signatures_nor_applies_the_design(generated, admin_member, director) -> None:
    """Since ASSUMPTIONS 418 only can_manage_signatories (the Director) adds signatures; the System Administrator still
    approves certificates (test_approvers_approve_all_for_the_activity)."""
    activity, _ = generated
    with pytest.raises(PermissionDenied):
        services.regenerate_certificates(admin_member, activity=activity)
    with pytest.raises(PermissionDenied, match="manage certificate signatories"):
        services.upload_signature(admin_member, activity, "left", png())
    assert services.upload_signature(director, activity, "left", png())["action"] == "replaced"


def test_system_administrator_role_may_combine_manage_users_and_certificate_approval(roles, admin_member) -> None:
    from accounts import services as account_services

    role = roles[Role.SYSTEM_ADMINISTRATOR]
    account_services.set_role_certificate_approval(admin_member, role, False)
    account_services.set_role_certificate_approval(admin_member, role, True)
    role.refresh_from_db()
    assert role.can_manage_users and role.can_approve_certificates
    with pytest.raises(account_services.MemberManagementError, match="approve documents"):
        account_services.set_role_document_approval(admin_member, role, True)


# --- Defaults and each activity's signatories ---------------------------------------------------------------------


def test_a_new_activity_starts_with_the_defaults(design, director, make_activity) -> None:
    _, vp, head = design
    activity = make_activity("Pitching Clinic")
    assert services.saved_activity_signatories(activity) == {"left": head, "right": vp}


def test_a_new_activity_without_defaults_has_empty_slots(director, make_activity) -> None:
    activity = make_activity("Pitching Clinic")
    assert services.saved_activity_signatories(activity) == {"left": None, "right": None}
    assert services.missing_slots_reason(services.activity_signatories(activity)) == \
        "Choose the two signatories on the Certificates tab first."


def test_two_activities_print_their_own_signatories(design, director, make_activity, conduct) -> None:
    """Activity 1: the Director and the VP (the defaults); activity 2: the Director and the University President."""
    _, vp, head = design
    president = person(director, "Dr. Ana Reyes", "University President")
    first = conduct(make_activity("Pitching Clinic"))
    second = make_activity("Investor Day")
    services.set_activity_signatory(director, second, "right", president)
    conduct(second)

    one = Certificate.objects.get(activity=first)
    two = Certificate.objects.get(activity=second)
    assert (one.left_signatory, one.right_signatory) == (head, vp)
    assert (two.left_signatory, two.right_signatory) == (head, president)
    html_one = pdf.certificate_html(one, name="X")
    html_two = pdf.certificate_html(two, name="X")
    assert "Vice President" in html_one and "University President" not in html_one
    assert "University President" in html_two and "Dr. Ana Reyes" in html_two and "Vice President" not in html_two
    # Left first, then right, each with its own name and position (no fixed "Vice President" / "Director" text).
    assert html_two.index(head.name) < html_two.index("Dr. Ana Reyes")


def test_changing_a_slot_refreshes_not_issued_certificates_and_writes_history(issued, design, director) -> None:
    activity, people = issued
    _, vp, head = design
    president = person(director, "Dr. Ana Reyes", "University President")
    waiting = certificate_of(people[1])
    result = services.set_activity_signatory(director, activity, "right", president)
    assert result == {"changed": True, "updated": 1, "outdated": 2, "cancelled": 1}  # outdated: the issued participant + speaker; the approval cancelled (369)
    waiting.refresh_from_db()
    assert waiting.right_signatory == president and waiting.status == S.GENERATED
    row = ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificate signatory (right)",
                                          changed_by=director).get()  # (the System's "default" row came first)
    assert (row.old_value, row.new_value, row.changed_by) == (
        "Dr. Vice Pres (Vice President)", "Dr. Ana Reyes (University President)", director)
    assert services.set_activity_signatory(director, activity, "right", president)["changed"] is False  # same person


def test_approved_certificates_wait_again_after_a_slot_change(evaluated, design, director, approve_all) -> None:
    activity, _ = evaluated
    approve_all(activity)
    president = person(director, "Dr. Pres", "University President")
    services.set_activity_signatory(director, activity, "right", president)
    assert set(Certificate.objects.values_list("status", "approved_by", "right_signatory")) == {(S.GENERATED, None, president.pk)}


def test_slot_rules(generated, design, director) -> None:
    activity, _ = generated
    _, vp, head = design
    with pytest.raises(services.CertificateError, match="already signs on the left"):
        services.set_activity_signatory(director, activity, "right", head)
    gone = person(director, "Gone", "Former Dean")
    services.deactivate_signatory(director, gone)
    with pytest.raises(services.CertificateError, match="inactive"):
        services.set_activity_signatory(director, activity, "left", gone)
    with pytest.raises(ValidationError, match="Choose left or right."):
        services.set_activity_signatory(director, activity, "middle", vp)
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():  # the database refuses it too
        ActivitySignatory.objects.filter(activity=activity, slot="right").update(signatory=head)


def test_a_deactivated_person_stays_on_existing_activities(generated, design, director) -> None:
    activity, _ = generated
    president = person(director, "Dr. Pres", "University President")
    services.set_activity_signatory(director, activity, "right", president)
    services.deactivate_signatory(director, president)
    assert services.activity_signatories(activity)["right"] == president
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "(inactive)" in html


def test_the_tab_choose_flow(generated, design, director) -> None:
    activity, _ = generated
    president = person(director, "Dr. Pres", "University President")
    client = client_for(director)
    html = client.get(tab_url(activity.pk)).content.decode()
    assert f'value="{president.pk}"' in html and "Who signs on the right" in html
    response = client.post(choose_url(activity, "right"), {"signatory": president.pk}, follow=True)
    assert [str(m) for m in response.context["messages"]] == [
        "Dr. Pres (University President) now signs on the right for this activity. 3 certificates not issued yet now "
        "use it (the preview shows it)."]
    assert client.get(choose_url(activity, "right"))["Location"] == tab_url(activity.pk)  # a GET goes to the tab


def test_add_a_person_from_the_tab_chooses_them(generated, design, director) -> None:
    activity, _ = generated
    response = client_for(director).post(reverse("certificates:signatory_add"), {
        "name": "Dr. New", "position": "University President", "file": png(),
        "activity": activity.pk, "slot": "right", "next": tab_url(activity.pk),
    })
    assert response.status_code == 302 and response["Location"] == tab_url(activity.pk)
    new = CertificateSignatory.objects.get(name="Dr. New")
    assert services.saved_activity_signatories(activity)["right"] == new
    unsafe = client_for(director).post(reverse("certificates:signatory_add"), {
        "name": "Dr. X", "position": "P", "next": "https://evil.example.com/"})
    assert unsafe["Location"] == reverse("certificates:signatories")  # never an outside address


# --- Approval needs both signatures ------------------------------------------------------------------------------


@pytest.mark.parametrize(("slot", "message"), [("right", "Add the signature of Dr. Vice Pres (Vice President) first."),
                                               ("left", "Add the signature of Engr. Dee Rector (Director) first.")])
def test_approval_is_blocked_without_a_signature(generated, design, director, slot, message) -> None:
    activity, _ = generated
    _, vp, head = design
    without_image(vp if slot == "right" else head)
    assert services.approval_closed_reason(activity) == message
    with pytest.raises(services.CertificateError, match=message.replace("(", r"\(").replace(")", r"\)")):
        services.approve_activity_certificates(director, activity)
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}
    assert services.readiness(appreciation_of(activity)) == services.WAITING_FOR_SIGNATURES
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "Signature missing" in html


def test_certificates_can_be_generated_before_the_signatures_exist(conducted, design, director) -> None:
    activity, _ = conducted
    _, vp, head = design
    without_image(vp), without_image(head)
    assert services.generate_missing_certificates(activity)["total"] == 3
    assert services.approval_closed_reason(activity) == "Add the signature of Engr. Dee Rector (Director) first."
    assert services.preview_pdf(director, Certificate.objects.first()).startswith(b"%PDF-")  # blank signature lines


def test_no_certificates_without_both_signatories(conducted, director) -> None:
    from conftest import _stored
    from certificates.models import CertificateTemplate

    activity, _ = conducted
    CertificateTemplate.objects.create(name="D", file_path=_stored("certificate_templates/d.png", image_bytes()),
                                       uploaded_by=director, is_active=True)
    assert services.generation_closed_reason(activity) == "Choose the two signatories on the Certificates tab first."
    assert services.auto_generate_certificates(activity) == 0
    services.set_activity_signatory(director, activity, "left", person(director, "A", "Director"))
    services.set_activity_signatory(director, activity, "right", person(director, "B", "President"))
    assert services.auto_generate_certificates(activity) == 3


# --- New versions: replacing an image, editing a name or position --------------------------------------------------


def test_adding_a_missing_signature_fills_the_same_row_and_unblocks_approval(evaluated, design, director, caplog) -> None:
    activity, _ = evaluated
    _, vp, _ = design
    without_image(vp)
    with caplog.at_level("INFO", logger="certificates.audit"):
        result = services.upload_signature(director, activity, "right", jpg())
    assert result["action"] == "added" and result["signatory"].pk == vp.pk and result["updated"] == 3
    vp.refresh_from_db()
    assert vp.signature_image_path.name.endswith(".png") and vp.is_active  # stored as a transparent PNG (ASSUMPTIONS 366)
    assert f"Signature added: signatory_id={vp.pk} -> {vp.pk}" in caplog.text
    assert f"by member_id={director.pk}" in caplog.text and "@" not in caplog.text  # no email or name
    assert services.approval_closed_reason(activity) is None
    assert len(services.approve_activity_certificates(director, activity)) == 3


def test_replacing_an_image_is_a_new_version_for_every_activity(
    issued, design, director, make_activity, run_tasks, django_capture_on_commit_callbacks, caplog,
) -> None:
    activity, people = issued
    _, vp, head = design
    other = make_activity("Investor Day")  # a second activity (Draft) that uses the same VP
    assert services.saved_activity_signatories(other)["right"] == vp
    waiting = certificate_of(people[1])
    with caplog.at_level("INFO", logger="certificates.audit"), django_capture_on_commit_callbacks(execute=True):
        result = services.replace_signature(director, vp, png())
    new = result["signatory"]
    assert result["action"] == "replaced" and new.pk != vp.pk
    assert (result["activities"], result["updated"], result["outdated"]) == (2, 1, 2)
    vp.refresh_from_db()
    assert (new.name, new.position, new.is_active, new.default_slot) == (vp.name, vp.position, True, "right")
    assert not vp.is_active and vp.default_slot is None
    assert services.saved_activity_signatories(activity)["right"] == new
    assert services.saved_activity_signatories(other)["right"] == new
    waiting.refresh_from_db()
    assert waiting.right_signatory == new
    issued_one = certificate_of(people[0])
    assert (issued_one.status, issued_one.right_signatory_id) == (S.ISSUED, vp.pk)  # keeps the old image
    assert new.signature_image_path.name != vp.signature_image_path.name
    assert f"Signature replaced: signatory_id={vp.pk} -> {new.pk}" in caplog.text
    assert ActivityAuditLog.objects.filter(field_changed="Certificate signatory (right)",
                                           new_value__endswith="new version").count() == 2


def test_editing_an_unused_person_is_in_place(director) -> None:
    unused = person(director, "Dr. Typo", "Presidnet")
    result = services.edit_signatory(director, unused, "Dr. Fixed", "President")
    assert result["action"] == "edited" and result["signatory"].pk == unused.pk
    assert services.edit_signatory(director, unused, "Dr. Fixed", "President")["action"] == "unchanged"


def test_editing_a_used_person_is_a_new_version(issued, design, director) -> None:
    activity, people = issued
    _, vp, _ = design
    result = services.edit_signatory(director, vp, vp.name, "Vice President for Research and Extension")
    new = result["signatory"]
    assert result["action"] == "new_version" and new.pk != vp.pk
    assert new.signature_image_path.name == vp.signature_image_path.name  # the image stays
    assert certificate_of(people[1]).right_signatory == new
    assert certificate_of(people[0]).right_signatory_id == vp.pk  # issued: old name kept, now outdated
    assert services.outdated_ids(activity) == {certificate_of(people[0]).pk, appreciation_of(activity).pk}
    with pytest.raises(services.CertificateError, match="Inactive"):
        services.edit_signatory(director, vp, "X", "Y")  # the old version can no longer be edited


def test_the_impact_is_shown_before_a_new_version_is_saved(issued, design, director, make_activity) -> None:
    """Addition A: "This updates N activities. X certificates not issued yet will use the new version; Y issued
    certificates will be marked 'Issued with an old signature'." On the tab, the Signatories page, and it matches."""
    activity, people = issued
    _, vp, _ = design
    make_activity("Investor Day")  # another activity using the VP, without certificates
    impact = services.version_impact(vp)
    assert impact == {"activities": 2, "not_issued": 1, "issued": 2}
    text = ("This updates 2 activities. 1 certificate not issued yet will use the new version; 2 issued certificates "
            "will be marked 'Issued with an old signature'.")
    assert services.version_impact_text(impact) == text
    escaped = text.replace("'", "&#x27;")
    tab = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert escaped in tab  # in the Replace signature dialog
    page = client_for(director).get(reverse("certificates:signatories")).content.decode()
    assert escaped in page and "Saving creates a new version of Dr. Vice Pres." in page
    result = services.replace_signature(director, vp, png())
    assert (result["activities"], result["updated"], result["outdated"]) == (2, 1, 2)  # what the dialog said


def test_signature_images_are_checked(generated, director) -> None:
    activity, _ = generated
    for file, error in [
        (None, "Choose an image to upload."),
        (upload("sig.gif", b"GIF89a"), "Only JPG or PNG images can be uploaded."),
        (upload("sig.png", b"not really a png"), "not a valid PNG image"),
        (upload("sig.jpg", image_bytes(40, 20)), "not a valid JPG image"),  # a PNG named .jpg
    ]:
        with pytest.raises(ValidationError, match=error):
            services.upload_signature(director, activity, "right", file)
    with pytest.raises(ValidationError, match="Choose left or right."):
        services.upload_signature(director, activity, "president", png())


def test_signature_size_limit(generated, director, settings) -> None:
    settings.CERTIFICATE_IMAGE_MAX_MB = 0
    with pytest.raises(ValidationError, match="larger than 0 MB"):
        services.upload_signature(director, generated[0], "right", png())


def test_upload_page_flow(generated, design, director) -> None:
    activity, _ = generated
    client = client_for(director)
    html = client.get(tab_url(activity.pk)).content.decode()
    assert 'id="signatures-title"' in html and "Replace signature" in html
    assert reverse("certificates:signature_image", args=[design[1].pk]) in html  # thumbnail for approvers
    response = client.post(signature_url(activity, "left"), {"file": png()}, follow=True)
    messages = [str(m) for m in response.context["messages"]]
    assert messages == ["Replaced the signature of Engr. Dee Rector (Director). 3 certificates not issued yet now use "
                        "it (the preview shows it)."]
    response = client.post(signature_url(activity, "right"), {"file": upload("x.png", b"nope")}, follow=True)
    assert "not a valid PNG image" in " ".join(str(m) for m in response.context["messages"])
    assert client.get(signature_url(activity, "right"))["Location"] == tab_url(activity.pk)  # a GET goes to the tab


def test_signature_thumbnail_needs_approval_or_admin_rights(design, director, view_only, admin_member) -> None:
    url = reverse("certificates:signature_image", args=[design[1].pk])
    assert client_for(director).get(url).status_code == 200
    assert client_for(admin_member).get(url).status_code == 200
    assert client_for(view_only).get(url).status_code == 403
    without_image(design[1])
    assert client_for(director).get(url).status_code == 404


# --- The Signatories page and the Admin Console ------------------------------------------------------------------


def test_signatories_page_flow(design, director) -> None:
    _, vp, head = design
    client = client_for(director)
    html = client.get(reverse("certificates:signatories")).content.decode()
    assert vp.name in html and "Default right" in html and "Default left" in html
    response = client.post(reverse("certificates:signatory_add"), {"name": "Dr. P", "position": "University President"})
    assert response.status_code == 302
    president = CertificateSignatory.objects.get(name="Dr. P")
    response = client.post(reverse("certificates:signatory_defaults"), {"left": head.pk, "right": president.pk}, follow=True)
    assert "Saved the default signatories." in " ".join(str(m) for m in response.context["messages"])
    assert services.default_signatories()["right"] == president
    response = client.post(reverse("certificates:signatory_deactivate", args=[president.pk]), follow=True)
    assert "Choose another default first" in " ".join(str(m) for m in response.context["messages"])
    response = client.post(reverse("certificates:signatory_add"), {"name": "", "position": ""})
    assert response.status_code == 400 and "This field is required." in response.content.decode()
    response = client.post(reverse("certificates:signatory_edit", args=[president.pk]), {"name": "Dr. P. Reyes",
                                                                                        "position": "University President"})
    president.refresh_from_db()
    assert response.status_code == 302 and president.name == "Dr. P. Reyes"  # unused: in place
    response = client.post(reverse("certificates:signatory_image", args=[president.pk]), {"file": png()})
    president.refresh_from_db()
    assert response.status_code == 302 and president.signature_image_path


def test_admin_console_shows_the_defaults_read_only(design, admin_member, make_member) -> None:
    _, vp, head = design
    html = client_for(admin_member).get(reverse("accounts:console_certificates")).content.decode()
    assert "Default signatories" in html and vp.name in html and head.name in html
    assert reverse("certificates:signatories") not in html  # only can_manage_signatories (ASSUMPTIONS 418)
    assert "console/certificates/signatories" not in html  # no add / deactivate forms any more


# --- The large preview on the tab ---------------------------------------------------------------------------------


def test_tab_shows_the_first_certificate_large_with_previous_next(generated, director, view_only) -> None:
    activity, people = generated
    first = Certificate.objects.filter(activity=activity).order_by("certificate_number").first()
    for member in (director, view_only):
        html = client_for(member).get(tab_url(activity.pk)).content.decode()
        assert 'id="certificate-preview"' in html and "Previous" in html and "Next" in html
        assert f'src="{reverse("certificates:preview", args=[first.pk])}?v=' in html
        assert "The final name comes from the evaluation." in html
        assert f"$dispatch('certificate-preview', {first.pk})" in html  # the row's Preview shows it here
        assert f'href="{reverse("certificates:preview_dialog", args=[first.pk])}"' in html  # without JavaScript


def test_preview_items_know_when_the_name_is_final(generated, evaluate, run_tasks) -> None:
    from certificates.pages import preview_items, printed_names

    activity, people = generated
    evaluate(people[0], name="Ana Final")
    certificates = list(Certificate.objects.filter(activity=activity))
    items = {item["pk"]: item for item in preview_items(certificates, printed_names(certificates), {})}
    assert items[certificate_of(people[0]).pk]["name_final"] is True
    assert items[certificate_of(people[1]).pk]["name_final"] is False
    assert items[appreciation_of(activity).pk]["name_final"] is True


def test_preview_is_marked_until_approved(generated, director, approve_all, evaluate) -> None:
    activity, people = generated
    evaluate(people[0])
    certificate = certificate_of(people[0])
    assert services.preview_marked(certificate)
    assert 'class="preview-mark"' in services.pdf.certificate_html(certificate, name="X", mark=True)
    approve_all(activity)
    certificate.refresh_from_db()
    assert not services.preview_marked(certificate)


# --- Needs attention ----------------------------------------------------------------------------------------------


def test_needs_attention_has_one_certificate_line(generated, design, director) -> None:
    activity, _ = generated
    texts = [item.text for item in attention.activity_attention(activity)]
    assert "Certificates need approval." in texts  # one approval per activity (ASSUMPTIONS 369)
    without_image(design[1])
    texts = [item.text for item in attention.activity_attention(activity)]
    assert "Add the signature of Dr. Vice Pres (Vice President) first." in texts
    assert "Certificates need approval." not in texts
    assert attention.attention_counts([activity])[activity.pk] == len(texts)
