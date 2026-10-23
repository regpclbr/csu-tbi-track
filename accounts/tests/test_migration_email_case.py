"""Migration accounts/0002: TBI_MEMBER emails become unique ignoring case. Existing case duplicates
stop the migration with their member IDs, and nothing changes."""

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction

from accounts.models import TBIMember

CONSTRAINT = "unique_member_email_ignoring_case"


def constraints() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.get_constraints(cursor, "tbi_member"))


def test_database_refuses_emails_that_differ_only_in_case(db, make_member) -> None:
    make_member("Director", email="ana@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        TBIMember.objects.create(email="ANA@example.com", name="Ana again", role=TBIMember.objects.get().role)


@pytest.mark.django_db(transaction=True)
def test_migration_checks_first_then_adds_the_index(make_member, capsys) -> None:
    make_member("Director", email="ana@example.com")
    make_member("Director", email="ben@example.com")
    try:
        call_command("migrate", "accounts", "0001", verbosity=0)
        assert CONSTRAINT not in constraints()
        call_command("migrate", "accounts", verbosity=0)
    finally:
        call_command("migrate", verbosity=0)
    assert "Checked 2 member(s): no emails that differ only in case." in capsys.readouterr().out
    assert CONSTRAINT in constraints()


@pytest.mark.django_db(transaction=True)
def test_migration_stops_with_the_member_ids_and_changes_nothing(make_member) -> None:
    first = make_member("Director", email="ana@example.com")
    try:
        call_command("migrate", "accounts", "0001", verbosity=0)
        second = make_member("Admin Staff", email="other@example.com")
        TBIMember.objects.filter(pk=second.pk).update(email="Ana@Example.com")  # possible only without the index
        with pytest.raises(RuntimeError, match=rf"ana@example.com: member IDs {first.pk}, {second.pk}"):
            call_command("migrate", "accounts", verbosity=0)
        assert CONSTRAINT not in constraints()  # rolled back: nothing changed
        second.delete()
    finally:
        call_command("migrate", verbosity=0)
    assert CONSTRAINT in constraints()
