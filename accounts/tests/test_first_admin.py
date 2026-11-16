"""create_first_admin: the first System Administrator of a clean database on the hosted review version (ASSUMPTIONS 395)."""

import logging
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from accounts.management.commands import create_first_admin as command_module
from accounts.models import Role, TBIMember
from accounts.services import FIRST_ADMIN_EXISTS, MemberManagementError, create_first_admin

pytestmark = pytest.mark.django_db

PASSWORD = "Kalinaw-Sapa-2026!"


def run(monkeypatch, answers: list[str], passwords: list[str], **options) -> str:
    """Run the command with typed answers (email, name) and passwords; return what it printed."""
    answers, passwords = iter(answers), iter(passwords)
    monkeypatch.setattr(command_module.Command, "ask", lambda self, prompt: next(answers))
    monkeypatch.setattr(command_module.Command, "ask_password", lambda self, prompt: next(passwords))
    out = StringIO()
    call_command("create_first_admin", stdout=out, **options)
    return out.getvalue()


def test_creates_the_first_system_administrator(roles, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="accounts.audit"):
        output = run(monkeypatch, ["Admin@CSU.edu.ph ", " Regine Admin "], [PASSWORD, PASSWORD])
    member = TBIMember.objects.get()
    assert member.email == "admin@csu.edu.ph" and member.name == "Regine Admin"
    assert member.role.role_name == Role.SYSTEM_ADMINISTRATOR and member.role.can_manage_users
    assert member.is_active and member.check_password(PASSWORD)
    assert member.must_change_password is False  # typed by the person themselves
    assert PASSWORD not in output and "admin@csu.edu.ph" in output
    assert PASSWORD not in caplog.text and "First System Administrator created" in caplog.text


def test_email_and_name_can_be_options(roles, monkeypatch) -> None:
    run(monkeypatch, [], [PASSWORD, PASSWORD], email="it@csu.edu.ph", name="IT Office")
    assert TBIMember.objects.get().email == "it@csu.edu.ph"


def test_refused_when_a_system_administrator_already_exists(admin_member, monkeypatch) -> None:
    with pytest.raises(CommandError, match="already exists"):
        run(monkeypatch, ["second@csu.edu.ph", "Second"], [PASSWORD, PASSWORD])
    assert TBIMember.objects.count() == 1


def test_an_inactive_administrator_does_not_block_it(admin_member, monkeypatch) -> None:
    TBIMember.objects.filter(pk=admin_member.pk).update(is_active=False)
    run(monkeypatch, ["new@csu.edu.ph", "New Admin"], [PASSWORD, PASSWORD])
    assert TBIMember.objects.filter(email="new@csu.edu.ph", is_active=True).exists()


def test_passwords_must_match(roles, monkeypatch) -> None:
    with pytest.raises(CommandError, match="do not match"):
        run(monkeypatch, ["a@csu.edu.ph", "A"], [PASSWORD, PASSWORD + "x"])
    assert not TBIMember.objects.exists()


@pytest.mark.parametrize("password", ["12345678", "qwertyuiop", "Ab1!"])
def test_weak_passwords_are_refused_without_echoing_them(roles, monkeypatch, password) -> None:
    with pytest.raises(CommandError, match="Nothing was created") as refused:
        run(monkeypatch, ["a@csu.edu.ph", "A"], [password, password])
    assert password not in str(refused.value)
    assert not TBIMember.objects.exists()


@pytest.mark.parametrize("email, name, message", [
    ("not-an-email", "A", "valid email"),
    ("a@csu.edu.ph", "   ", "Enter the name"),
])
def test_bad_email_or_empty_name_is_refused(roles, email, name, message) -> None:
    with pytest.raises(MemberManagementError, match=message):
        create_first_admin(email=email, name=name, password=PASSWORD)


def test_without_the_seeded_roles_it_says_to_run_seed_data() -> None:
    with pytest.raises(MemberManagementError, match="seed_data"):
        create_first_admin(email="a@csu.edu.ph", name="A", password=PASSWORD)


def test_cancelled_input_creates_nothing(roles, monkeypatch) -> None:
    def cancel(self, prompt):
        raise EOFError

    monkeypatch.setattr(command_module.Command, "ask", cancel)
    with pytest.raises(CommandError, match="Cancelled"):
        call_command("create_first_admin", stdout=StringIO())
    assert not TBIMember.objects.exists()


def test_the_refusal_message_points_to_the_admin_console() -> None:
    assert "Admin Console" in FIRST_ADMIN_EXISTS
