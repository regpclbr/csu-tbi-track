"""Certificate approvers and signatures (ASSUMPTIONS 361): who approves and adds signatures, adding / replacing a
signature on the Certificates tab (and what it does to the activity's certificates), approval blocked until both
signatures are there, the audit line, the large preview on the tab and the Needs attention line."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from activities import attention
from certificates import services
from certificates.models import Certificate, CertificateSignatory
from certificates.pages import tab_url

from .conftest import S, appreciation_of, certificate_of, upload
from conftest import image_bytes

pytestmark = pytest.mark.django_db

APPROVERS = ["Director", "Incubation Project Head", "Community Development Head", Role.SYSTEM_ADMINISTRATOR]


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


def signature_url(activity, position: str) -> str:
    return reverse("activities:certificates_signature", args=[activity.pk, position])


# --- Who may approve and add signatures --------------------------------------------------------------------------


def test_the_seeded_approver_roles_have_the_flag(roles) -> None:
    assert {name for name, role in roles.items() if role.can_approve_certificates} == set(APPROVERS)


@pytest.mark.parametrize("role_name", APPROVERS)
def test_approvers_approve_all_for_the_activity(generated, make_member, role_name) -> None:
    activity, _ = generated
    member = make_member(role_name, email=f"approver.{role_name.split()[0].lower()}@example.com")
    assert services.approve_certificates(member, activity=activity) == 3
    assert set(Certificate.objects.values_list("status", "approved_by")) == {(S.APPROVED, member.pk)}


def test_admin_staff_only_views(generated, admin_staff) -> None:
    activity, _ = generated
    with pytest.raises(PermissionDenied):
        services.approve_certificates(admin_staff, activity=activity)
    with pytest.raises(PermissionDenied):
        services.upload_signature(admin_staff, activity, "vp", png())
    client = client_for(admin_staff)
    assert client.post(reverse("activities:certificates_approve_all", args=[activity.pk])).status_code == 403
    assert client.post(signature_url(activity, "vp"), {"file": png()}).status_code == 403
    html = client.get(tab_url(activity.pk)).content.decode()
    assert "Waiting for approval" in html and "Approve all for this activity" not in html
    assert 'name="file"' not in html.split('id="signatures-title"')[1].split("certificate-preview")[0]
    assert "A member with approval rights adds it." not in html  # both signatures are there


def test_system_administrator_approves_and_adds_signatures_but_cannot_generate(conducted, design, admin_member) -> None:
    activity, _ = conducted
    with pytest.raises(PermissionDenied):
        services.generate_certificates(admin_member, activity)
    result = services.upload_signature(admin_member, activity, "director", png())
    assert result["action"] == "replaced"


def test_system_administrator_role_may_combine_manage_users_and_certificate_approval(roles, admin_member) -> None:
    from accounts import services as account_services

    role = roles[Role.SYSTEM_ADMINISTRATOR]
    account_services.set_role_certificate_approval(admin_member, role, False)
    account_services.set_role_certificate_approval(admin_member, role, True)
    role.refresh_from_db()
    assert role.can_manage_users and role.can_approve_certificates
    with pytest.raises(account_services.MemberManagementError, match="approve documents"):
        account_services.set_role_document_approval(admin_member, role, True)


# --- Approval needs both signatures ------------------------------------------------------------------------------


@pytest.mark.parametrize(("position", "message"), [("vp", "Add the VP's signature first."),
                                                     ("director", "Add the Director's signature first.")])
def test_approval_is_blocked_without_a_signature(generated, design, director, position, message) -> None:
    activity, _ = generated
    _, vp, head = design
    without_image(vp if position == "vp" else head)
    assert services.approval_closed_reason(activity) == message
    with pytest.raises(services.CertificateError, match=message):
        services.approve_certificates(director, activity=activity)
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert message.replace("'", "&#x27;") in html and "Missing" in html


def test_certificates_can_be_generated_before_the_signatures_exist(conducted, design, director) -> None:
    activity, _ = conducted
    _, vp, head = design
    without_image(vp), without_image(head)
    assert services.generate_certificates(director, activity)["total"] == 3
    assert services.approval_closed_reason(activity) == "Add the VP's signature first."
    assert services.preview_pdf(director, Certificate.objects.first()).startswith(b"%PDF-")  # blank signature lines


# --- Adding and replacing a signature ----------------------------------------------------------------------------


def test_adding_a_missing_signature_fills_the_same_row_and_unblocks_approval(generated, design, director, caplog) -> None:
    activity, _ = generated
    _, vp, _ = design
    without_image(vp)
    with caplog.at_level("INFO", logger="certificates.audit"):
        result = services.upload_signature(director, activity, "vp", jpg())
    assert result["action"] == "added" and result["signatory"].pk == vp.pk and result["updated"] == 3
    vp.refresh_from_db()
    assert vp.signature_image_path.name.endswith(".jpg") and vp.is_active
    assert CertificateSignatory.objects.filter(position="vp").count() == 1
    assert f"Signature added: position=vp signatory_id={vp.pk} -> {vp.pk} activity_id={activity.pk}" in caplog.text
    assert f"by member_id={director.pk}" in caplog.text and "@" not in caplog.text  # no email or name
    assert services.approval_closed_reason(activity) is None
    assert services.approve_certificates(director, activity=activity) == 3


def test_replacing_a_signature_creates_a_new_row_and_sends_approved_back(
    generated, design, director, approve_all, run_tasks, django_capture_on_commit_callbacks, caplog,
) -> None:
    activity, people = generated
    _, vp, _ = design
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
    speaker = appreciation_of(activity)
    speaker.refresh_from_db()
    assert speaker.status == S.APPROVED and speaker.file_path  # rendered on approval
    old_file = speaker.file_path.name

    with caplog.at_level("INFO", logger="certificates.audit"), django_capture_on_commit_callbacks(execute=True):
        result = services.upload_signature(director, activity, "vp", png())
    new = result["signatory"]
    assert result["action"] == "replaced" and new.pk != vp.pk and result["updated"] == 3
    vp.refresh_from_db()
    assert new.is_active and not vp.is_active and new.name == vp.name
    for certificate in Certificate.objects.filter(activity=activity):
        assert (certificate.status, certificate.vp_signatory_id, certificate.approved_by_id) == (S.GENERATED, new.pk, None)
        assert not certificate.file_path
    assert not speaker.file_path.storage.exists(old_file)
    assert f"Signature replaced: position=vp signatory_id={vp.pk} -> {new.pk}" in caplog.text

    # Approved again: the new signature is the one printed.
    services.approve_certificates(director, activity=activity)
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.APPROVED}


def test_replacing_leaves_issued_certificates_and_other_activities_alone(
    generated, design, director, approve_all, evaluate, run_tasks, django_capture_on_commit_callbacks,
) -> None:
    activity, people = generated
    _, vp, _ = design
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
    issued = certificate_of(people[0])
    issued.refresh_from_db()
    assert issued.status == S.ISSUED

    result = services.upload_signature(director, activity, "vp", png())
    issued.refresh_from_db()
    assert (issued.status, issued.vp_signatory_id) == (S.ISSUED, vp.pk)
    assert result["updated"] == 2


def test_signature_images_are_checked(generated, director) -> None:
    activity, _ = generated
    for file, error in [
        (None, "Choose an image to upload."),
        (upload("sig.gif", b"GIF89a"), "Only JPG or PNG images can be uploaded."),
        (upload("sig.png", b"not really a png"), "not a valid PNG image"),
        (upload("sig.jpg", image_bytes(40, 20)), "not a valid JPG image"),  # a PNG named .jpg
    ]:
        with pytest.raises(ValidationError, match=error):
            services.upload_signature(director, activity, "vp", file)
    with pytest.raises(ValidationError, match="Choose vp or director."):
        services.upload_signature(director, activity, "president", png())


def test_signature_size_limit(generated, director, settings) -> None:
    settings.CERTIFICATE_IMAGE_MAX_MB = 0
    with pytest.raises(ValidationError, match="larger than 0 MB"):
        services.upload_signature(director, generated[0], "vp", png())


def test_no_active_signatory_means_no_upload(conducted, director) -> None:
    activity, _ = conducted
    reason = "There is no active VP signatory yet. The System Administrator adds one in the Admin Console first."
    assert services.signature_closed_reason(activity, "vp") == reason
    with pytest.raises(services.CertificateError, match="no active VP signatory"):
        services.upload_signature(director, activity, "vp", png())


def test_upload_page_flow(generated, design, director) -> None:
    activity, _ = generated
    client = client_for(director)
    html = client.get(tab_url(activity.pk)).content.decode()
    assert 'id="signatures-title"' in html and "Replace the VP's signature" in html
    assert reverse("certificates:signature_image", args=[design[1].pk]) in html  # thumbnail for approvers
    response = client.post(signature_url(activity, "director"), {"file": png()}, follow=True)
    messages = [str(m) for m in response.context["messages"]]
    assert messages == ["Replaced the Director's signature. 3 certificates of this activity now use it; "
                        "approved ones wait for approval again."]
    response = client.post(signature_url(activity, "vp"), {"file": upload("x.png", b"nope")}, follow=True)
    assert "not a valid PNG image" in " ".join(str(m) for m in response.context["messages"])
    assert client.get(signature_url(activity, "vp")).status_code == 405


def test_signature_thumbnail_needs_approval_or_admin_rights(design, director, admin_staff, admin_member) -> None:
    url = reverse("certificates:signature_image", args=[design[1].pk])
    assert client_for(director).get(url).status_code == 200
    assert client_for(admin_member).get(url).status_code == 200
    assert client_for(admin_staff).get(url).status_code == 403
    without_image(design[1])
    assert client_for(director).get(url).status_code == 404


def test_admin_console_adds_a_signatory_without_an_image(admin_member) -> None:
    signatory = services.add_signatory(admin_member, "Dr. New VP", "vp", None)
    assert signatory.is_active and not signatory.signature_image_path
    html = client_for(admin_member).get(reverse("accounts:console_certificates")).content.decode()
    assert "No signature yet" in html


# --- The large preview on the tab ---------------------------------------------------------------------------------


def test_tab_shows_the_first_certificate_large_with_previous_next(generated, director, admin_staff) -> None:
    activity, people = generated
    first = Certificate.objects.filter(activity=activity).order_by("certificate_number").first()
    for member in (director, admin_staff):
        html = client_for(member).get(tab_url(activity.pk)).content.decode()
        assert 'id="certificate-preview"' in html and "Previous" in html and "Next" in html
        assert f'src="{reverse("certificates:preview", args=[first.pk])}"' in html
        assert "The final name comes from the evaluation." in html
        assert f"$dispatch('certificate-preview', {first.pk})" in html  # the row's Preview shows it here
        assert f'href="{reverse("certificates:preview_dialog", args=[first.pk])}"' in html  # without JavaScript


def test_preview_items_know_when_the_name_is_final(generated, evaluate, run_tasks) -> None:
    from certificates.pages import preview_items

    activity, people = generated
    evaluate(people[0], name="Ana Final")
    items = {item["pk"]: item for item in preview_items(list(Certificate.objects.filter(activity=activity)))}
    assert items[certificate_of(people[0]).pk]["name_final"] is True
    assert items[certificate_of(people[1]).pk]["name_final"] is False
    assert items[appreciation_of(activity).pk]["name_final"] is True


def test_preview_is_marked_until_approved(generated, director, approve_all) -> None:
    activity, people = generated
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
    assert "3 certificates waiting for approval." in texts
    without_image(design[1])
    texts = [item.text for item in attention.activity_attention(activity)]
    assert "Add the VP's signature first." in texts and "3 certificates waiting for approval." not in texts
    assert attention.attention_counts([activity])[activity.pk] == len(texts)
