"""Phase F6: the Admin Console pages (/accounts/console/...). Only can_manage_users; every action goes through the
services, whose refusals (last Admin, yourself) are shown; certificate designs and signatories are never deleted;
System status never calls Gemini and never shows a secret."""

from types import SimpleNamespace

import pytest
from django.core import mail
from django.core.management import call_command
from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django_q.models import Schedule, Task

from accounts import services, system_status
from accounts.models import TBIMember
from certificates.models import CertificateSignatory, CertificateTemplate
from certificates.tests.conftest import template_upload, upload
from conftest import image_bytes
from conftest import VIEW_ONLY_ROLE

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
CONSOLE_PAGES = ["accounts:console", "accounts:console_roles", "accounts:console_certificates", "accounts:console_system",
                 "accounts:console_system_pdf"]
CONSOLE_POSTS = [
    ("accounts:console_invite", []), ("accounts:console_member_role", [1]), ("accounts:console_member_deactivate", [1]),
    ("accounts:console_member_reactivate", [1]), ("accounts:console_member_reset", [1]), ("accounts:console_template_upload", []),
    ("accounts:console_template_action", [1, "activate"]),
]
SMTP_PASSWORD, SMTP_USER, GEMINI_KEY = "Sup3r-Secret-SMTP-pass", "smtp-login-secret", "AIza-very-secret-key-123"


def logged_in(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def admin(admin_member) -> Client:
    return logged_in(admin_member)


def toasts(client: Client, response) -> str:
    """The page after a redirect, with its toasts (Django messages)."""
    return client.get(response["Location"]).content.decode()


# --- Access: Admin only --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", CONSOLE_PAGES)
def test_console_pages_are_admin_only(client, admin_member, make_member, name) -> None:
    url = reverse(name)
    assert client.get(url).status_code == 302
    assert client.get(url, **HTMX).status_code == 401
    for role in ("Director", "Incubation Project Head", "Community Development Head", "Admin Staff"):
        assert logged_in(make_member(role)).get(url).status_code == 403
    assert logged_in(admin_member).get(url).status_code == 200


@pytest.mark.parametrize(("name", "args"), CONSOLE_POSTS)
def test_console_actions_are_admin_only(make_member, name, args) -> None:
    response = logged_in(make_member("Director")).post(reverse(name, args=args), {"name": "X", "email": "x@example.com"})
    assert response.status_code == 403
    assert TBIMember.objects.count() == 1 and not CertificateTemplate.objects.exists()


def test_the_sidebar_marks_the_console_on_every_tab(admin) -> None:
    for name in CONSOLE_PAGES[:4]:
        html = admin.get(reverse(name)).content.decode()
        assert 'aria-current="page"' in html.split("Admin Console</span>")[0].rsplit("<a", 1)[1]
        assert "is coming soon" not in html and reverse("startups:list") in html  # the Startups link


# --- Members ---------------------------------------------------------------------------------------------


def test_members_list_with_search_and_filters(admin, make_member, roles) -> None:
    director = make_member("Director")
    staff = make_member("Admin Staff", is_active=False)
    html = admin.get(reverse("accounts:console")).content.decode()
    assert director.email in html and staff.email in html and "Deactivated" in html and "(you)" in html
    partial = admin.get(reverse("accounts:console"), {"q": "director"}, **HTMX).content.decode()
    assert director.email in partial and staff.email not in partial and "<html" not in partial
    by_role = admin.get(reverse("accounts:console"), {"role": roles["Admin Staff"].pk}).content.decode()
    assert staff.email in by_role.split('id="member-results"')[1] and director.email not in by_role.split('id="member-results"')[1]
    inactive = admin.get(reverse("accounts:console"), {"is_active": "false"}).content.decode().split('id="member-results"')[1]
    assert staff.email in inactive and director.email not in inactive
    ignored = admin.get(reverse("accounts:console"), {"is_active": "maybe", "role": "x"}).content.decode()
    assert "could not be read and were ignored: active, role" in ignored


def test_list_members_service(admin_member, make_member, roles) -> None:
    make_member("Director")
    assert services.list_members({"q": "SYSTEM.ADMINISTRATOR@"}).total == 1
    assert services.list_members({"role": roles["Director"].pk}).items[0].role.role_name == "Director"
    assert services.list_members({"is_active": False}).total == 0


def test_invite_a_member_sends_the_invitation(admin, roles) -> None:
    response = admin.post(reverse("accounts:console_invite"), {"name": "Ana Cruz", "email": "Ana@Example.com", "role": roles["Director"].pk})
    member = TBIMember.objects.get(email="ana@example.com")
    assert member.must_change_password and member.role == roles["Director"]
    assert mail.outbox and mail.outbox[-1].to == ["ana@example.com"]
    assert "The invitation with a temporary password was emailed to ana@example.com" in toasts(admin, response)


@pytest.mark.parametrize(("data", "field", "message"), [
    ({"name": "Ana", "email": "ana@example.com", "role": ""}, "role", "Choose a role."),
    ({"name": "Ana", "email": "not-an-email", "role": "DIRECTOR"}, "email", "Enter a valid email address."),
    ({"name": "Again", "email": "SYSTEM.Administrator@example.com", "role": "DIRECTOR"}, "email", "A member with this email already exists."),
    ({"name": "  ", "email": "ana@example.com", "role": "DIRECTOR"}, "name", "Enter the member&#x27;s name."),
])
def test_invite_errors_appear_under_their_fields(admin, roles, data, field, message) -> None:
    if data["role"] == "DIRECTOR":
        data = {**data, "role": roles["Director"].pk}
    response = admin.post(reverse("accounts:console_invite"), data)
    html = response.content.decode()
    assert response.status_code == 400 and f'id="invite-{field}-error" class="field-error">{message}' in html
    assert 'id="invite-member" open' in html and TBIMember.objects.count() == 1 and not mail.outbox


def test_the_last_admin_guard_message_is_shown(admin, admin_member, roles) -> None:
    response = admin.post(reverse("accounts:console_member_role", args=[admin_member.pk]), {"role": roles["Director"].pk})
    assert "At least one active System Administrator must always exist." in toasts(admin, response)
    admin_member.refresh_from_db()
    assert admin_member.role.role_name == "System Administrator"


def test_you_cannot_deactivate_or_reset_yourself(admin, admin_member) -> None:
    response = admin.post(reverse("accounts:console_member_deactivate", args=[admin_member.pk]))
    assert "You cannot deactivate your own account." in toasts(admin, response)
    response = admin.post(reverse("accounts:console_member_reset", args=[admin_member.pk]))
    assert "Use Change password for your own account." in toasts(admin, response)
    admin_member.refresh_from_db()
    assert admin_member.is_active


def test_change_role_deactivate_reactivate_and_reset(admin, make_member, roles) -> None:
    other = make_member("Director")
    next_url = f"{reverse('accounts:console')}?q=director"
    response = admin.post(reverse("accounts:console_member_role", args=[other.pk]), {"role": roles["Admin Staff"].pk, "next": next_url})
    assert response["Location"] == next_url and "Test Director is now Admin Staff." in toasts(admin, response)
    response = admin.post(reverse("accounts:console_member_reset", args=[other.pk]))
    assert f"A new temporary password was emailed to {other.email}." in toasts(admin, response) and mail.outbox[-1].to == [other.email]
    admin.post(reverse("accounts:console_member_deactivate", args=[other.pk]))
    other.refresh_from_db()
    assert other.is_active is False
    response = admin.post(reverse("accounts:console_member_reset", args=[other.pk]))
    assert "Reactivate the account first." in toasts(admin, response)
    admin.post(reverse("accounts:console_member_reactivate", args=[other.pk]))
    other.refresh_from_db()
    assert other.is_active and TBIMember.objects.count() == 2  # never deleted
    assert admin.post(reverse("accounts:console_member_role", args=[other.pk]), {"role": "999999"}).status_code == 302
    other.refresh_from_db()
    assert other.role == roles["Admin Staff"]


def test_member_actions_go_back_only_within_this_site(admin, make_member) -> None:
    other = make_member("Director")
    response = admin.post(reverse("accounts:console_member_reactivate", args=[other.pk]), {"next": "https://evil.example.com/"})
    assert response["Location"] == reverse("accounts:console")


# --- Roles ------------------------------------------------------------------------------------------------


def test_roles_link_to_the_administration_site_and_switch_three_flags(admin, roles) -> None:
    html = admin.get(reverse("accounts:console_roles")).content.decode()
    assert all(name in html for name in roles) and reverse("admin:accounts_role_changelist") in html
    assert "Edit in the administration site" in html
    section = html.split('id="roles-title"')[1].split("</section>")[0]
    # The only forms are the four switches per role (create activities, ASSUMPTIONS 388; documents, 344; certificates,
    # 357; delete activities, 389); "Manage users" stays in the Django admin.
    assert section.count("<form") == 4 * len(roles) == section.count('name="enabled"')
    assert all(reverse("accounts:console_role_activity_deletion", args=[role.pk]) in section for role in roles.values())
    assert all(reverse("accounts:console_role_activity_creation", args=[role.pk]) in section for role in roles.values())
    assert all(reverse("accounts:console_role_approval", args=[role.pk]) in section for role in roles.values())
    assert all(reverse("accounts:console_role_certificate_approval", args=[role.pk]) in section for role in roles.values())


# --- Certificate design: never deleted ---------------------------------------------------------------------


def test_upload_a_design_with_the_image_checks(admin) -> None:
    url = reverse("accounts:console_template_upload")
    bad = admin.post(url, {"name": "Too small", "file": template_upload(size=(800, 600)), "activate": "true"})
    assert bad.status_code == 400 and 'open id="upload-template"' in bad.content.decode()
    assert 'id="template-file-error"' in bad.content.decode() and not CertificateTemplate.objects.exists()
    nameless = admin.post(url, {"name": "", "file": template_upload()})
    assert 'id="template-name-error" class="field-error">This field is required.' in nameless.content.decode()
    response = admin.post(url, {"name": "TBI 2026", "file": template_upload(), "activate": "true"})
    template = CertificateTemplate.objects.get()
    assert template.is_active and "is now the active one" in toasts(admin, response)
    html = admin.get(reverse("accounts:console_certificates")).content.decode()
    assert f'src="{reverse("certificates:template_image", args=[template.pk])}"' in html  # the Admin-only view


def test_activate_and_deactivate_designs_but_never_delete(admin, admin_member) -> None:
    for name in ("Old", "New"):
        admin.post(reverse("accounts:console_template_upload"), {"name": name, "file": template_upload(), "activate": "true"})
    old, new = CertificateTemplate.objects.order_by("pk")
    assert not CertificateTemplate.objects.get(pk=old.pk).is_active
    admin.post(reverse("accounts:console_template_action", args=[old.pk, "activate"]))
    assert list(CertificateTemplate.objects.filter(is_active=True)) == [old]
    response = admin.post(reverse("accounts:console_template_action", args=[old.pk, "deactivate"]))
    assert "Certificates cannot be generated until a design is active." in toasts(admin, response)
    assert admin.post(reverse("accounts:console_template_action", args=[new.pk, "delete"])).status_code == 404
    assert admin.post(f"/accounts/console/certificates/templates/{new.pk}/").status_code in (404, 405)
    assert CertificateTemplate.objects.count() == 2
    assert "No design is active" in admin.get(reverse("accounts:console_certificates")).content.decode()


def test_console_shows_the_default_signatories_read_only(admin) -> None:
    """ASSUMPTIONS 363: the list and the defaults are managed on /certificates/signatories/ (can_approve_certificates);
    the Admin Console only shows the defaults, with a link. Signatories are never deleted (no delete URL anywhere)."""
    html = admin.get(reverse("accounts:console_certificates")).content.decode()
    assert "Default signatories" in html and "Not set" in html
    assert reverse("certificates:signatories") in html  # the System Administrator approves certificates by default
    left = CertificateSignatory.objects.create(name="Dr. Left", position="Director", default_slot="left")
    CertificateSignatory.objects.create(name="Dr. Right", position="University President", default_slot="right")
    html = admin.get(reverse("accounts:console_certificates")).content.decode()
    assert "Dr. Left" in html and "University President" in html and "no signature image yet" in html
    assert 'name="position"' not in html  # nothing to edit here
    for name in ("accounts:console_signatory_add", "accounts:console_signatory_delete", "certificates:signatory_delete"):
        with pytest.raises(NoReverseMatch):
            reverse(name, args=[left.pk]) if "delete" in name else reverse(name)
    assert admin.post(f"/certificates/signatories/{left.pk}/delete/").status_code == 404
    assert CertificateSignatory.objects.count() == 2


def test_design_images_are_never_served_to_non_admins(admin, make_member) -> None:
    admin.post(reverse("accounts:console_template_upload"), {"name": "D", "file": template_upload(), "activate": "true"})
    template = CertificateTemplate.objects.get()
    assert admin.get(reverse("certificates:template_image", args=[template.pk])).status_code == 200
    assert logged_in(make_member("Director")).get(reverse("certificates:template_image", args=[template.pk])).status_code == 403


# --- System status: never Gemini, never secrets --------------------------------------------------------------


@pytest.fixture
def no_gemini(monkeypatch):
    """Any Gemini use fails the test (the page must only read whether a key is set)."""
    from assistant import gemini

    def refuse(*args, **kwargs):
        raise AssertionError("System status must never call Gemini.")

    monkeypatch.setattr(gemini, "get_gateway", refuse)


def test_system_status_never_calls_gemini_and_never_shows_secrets(admin, settings, no_gemini, fake_gemini) -> None:
    settings.GEMINI_API_KEY = GEMINI_KEY
    settings.MAILERS = {"default": {"BACKEND": "config.mail.GuardedSMTPBackend", "OPTIONS": {
        "host": "smtp.example.com", "port": 587, "username": SMTP_USER, "password": SMTP_PASSWORD, "use_tls": True}}}
    html = admin.get(reverse("accounts:console_system")).content.decode()
    assert "An API key is set (not tested on this page)." in html and "python manage.py check_gemini" in html
    assert "Real SMTP" in html and "smtp.example.com:587" in html
    for secret in (GEMINI_KEY, SMTP_PASSWORD, SMTP_USER):
        assert secret not in html
    assert not fake_gemini.generate_calls and not fake_gemini.embed_calls  # nothing reached the (fake) Gemini either
    assert 'hx-get="/accounts/console/system/pdf/"' in html and 'hx-trigger="load"' in html


def test_email_in_development_mode_is_blue(admin, settings) -> None:
    settings.DEBUG = True
    settings.MAILERS = {"default": settings.CONSOLE_MAILER}
    item = system_status.email_status()
    assert item.tone == system_status.INFO and item.summary == "Development mode: emails are printed in the terminal."
    html = admin.get(reverse("accounts:console_system")).content.decode()
    status_email = html.split('id="status-email"')[0].rsplit("<li", 1)[1]
    assert "border-info-line" in status_email and "Development mode: emails are printed in the terminal." in html


def test_email_console_mode_without_debug_is_amber_with_the_fix(admin, settings) -> None:
    settings.DEBUG = False
    settings.MAILERS = {"default": settings.CONSOLE_MAILER}
    item = system_status.email_status()
    assert item.tone == system_status.WARNING and "EMAIL_REAL_SENDING=True" in item.fix
    html = admin.get(reverse("accounts:console_system")).content.decode()
    assert "border-warning-line" in html.split('id="status-email"')[0].rsplit("<li", 1)[1]


def test_gemini_without_a_key_is_amber(settings) -> None:
    settings.GEMINI_API_KEY = ""
    item = system_status.gemini_status()
    assert item.tone == system_status.WARNING and "check_gemini" in item.fix


def test_qcluster_running_or_not(monkeypatch) -> None:
    from django_q.status import Stat

    monkeypatch.setattr(Stat, "get_all", staticmethod(lambda broker=None: []))
    assert system_status.qcluster_status().tone == system_status.WARNING
    assert "python manage.py qcluster" in system_status.qcluster_status().fix
    monkeypatch.setattr(Stat, "get_all", staticmethod(lambda broker=None: [SimpleNamespace(workers=[1, 2])]))
    item = system_status.qcluster_status()
    assert item.tone == system_status.OK and item.summary == "Running: 1 cluster, 2 workers."


def test_schedules_missing_set_up_and_failed() -> None:
    item = system_status.schedule_status()
    assert item.tone == system_status.WARNING and "setup_schedules" in item.fix
    call_command("setup_schedules", stdout=None)
    item = system_status.schedule_status()
    assert item.tone == system_status.OK and all(row["last_result"] == "Not run yet" and row["next_run"] for row in item.rows)
    schedule = Schedule.objects.first()
    now = timezone.now()
    Task.objects.create(id="t-failed", name="x", func=schedule.func, started=now, stopped=now, success=False)
    Schedule.objects.filter(pk=schedule.pk).update(task="t-failed")
    item = system_status.schedule_status()
    assert item.tone == system_status.WARNING and "Failed tasks" in item.fix
    assert [row["last_result"] for row in item.rows].count("Failed") == 1


def test_pdf_check_uses_the_renderer_and_shows_the_fix(admin, fake_pdf_renderer) -> None:
    html = admin.get(reverse("accounts:console_system_pdf")).content.decode()
    assert "A test PDF was rendered in memory." in html and fake_pdf_renderer.rendered == [system_status.PDF_TEST_HTML]
    fake_pdf_renderer.fail = OSError("cannot load library 'libgobject-2.0-0'")
    html = admin.get(reverse("accounts:console_system_pdf")).content.decode()
    assert "PDFs cannot be made: OSError." in html and "python manage.py check_pdf" in html


def test_counts_of_unindexed_documents_and_unsent_certificates(monkeypatch) -> None:
    assert system_status.indexing_status().tone == system_status.OK
    assert system_status.certificate_email_status().tone == system_status.OK
    from assistant import indexing
    from certificates import services as certificate_services

    monkeypatch.setattr(indexing, "pending_documents", lambda: SimpleNamespace(count=lambda: 3))
    monkeypatch.setattr(certificate_services, "pending_emails", lambda: SimpleNamespace(count=lambda: 1))
    assert system_status.indexing_status().summary == "3 approved documents are not indexed yet."
    assert system_status.certificate_email_status().summary == "1 issued certificate is not sent yet."
    assert "retry_certificates" in system_status.certificate_email_status().fix
