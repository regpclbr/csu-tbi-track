"""Migrations registrations/0004-0005: existing evaluations get certificate_name and certificate_email
from their registration; if one cannot be filled, the migration stops and changes nothing."""

import pytest
from django.core.management import call_command
from django.db import connection

from registrations import services
from registrations.models import EvaluationSubmission, Registration

from .conftest import CERT, evaluation_answers, opens_at


def migrate_back() -> None:
    call_command("migrate", "certificates", "0001", verbosity=0)
    call_command("migrate", "registrations", "0003", verbosity=0)


def migrate_forward() -> None:
    call_command("migrate", verbosity=0)


def columns() -> set[str]:
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, "evaluation_submission")}


@pytest.mark.django_db(transaction=True)
def test_fill_from_registration_then_not_null(attended, capsys) -> None:
    person = attended(1)[0]
    services.submit_evaluation(services.evaluation_token(person), **CERT, answers=evaluation_answers(person.activity), now=opens_at(person.activity))
    Registration.objects.filter(pk=person.pk).update(name="  Ana Cruz ", email=" Ana@Example.com ")
    try:
        migrate_back()
        assert "certificate_name" not in columns()
        migrate_forward()
    finally:
        migrate_forward()
    assert "Evaluation submissions: 1. Filled from the registration: 1. Could not fill: 0." in capsys.readouterr().out
    submission = EvaluationSubmission.objects.get()
    assert (submission.certificate_name, submission.certificate_email) == ("Ana Cruz", "ana@example.com")
    with connection.cursor() as cursor:
        nullable = {c.name: c.null_ok for c in connection.introspection.get_table_description(cursor, "evaluation_submission")}
    assert nullable["certificate_name"] is False and nullable["certificate_email"] is False


@pytest.mark.django_db(transaction=True)
def test_stops_and_changes_nothing_when_a_row_cannot_be_filled(attended) -> None:
    person = attended(1)[0]
    services.submit_evaluation(services.evaluation_token(person), **CERT, answers=evaluation_answers(person.activity), now=opens_at(person.activity))
    submission_id = EvaluationSubmission.objects.get().pk
    Registration.objects.filter(pk=person.pk).update(name="   ")
    try:
        migrate_back()
        with pytest.raises(RuntimeError, match=f"could not be filled.*{submission_id}"):
            migrate_forward()
        assert "certificate_name" not in columns()  # 0004 rolled back: nothing changed
    finally:
        Registration.objects.filter(pk=person.pk).update(name="Ana Cruz")
        migrate_forward()
    assert "certificate_name" in columns()
