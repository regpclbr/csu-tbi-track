"""Database constraints on registrations and form answers."""

import pytest
from django.db import IntegrityError

from registrations.models import (
    EvaluationSubmission,
    FieldType,
    FormAnswer,
    FormField,
    FormTemplate,
    FormType,
    Registration,
)


@pytest.mark.django_db
def test_one_registration_per_activity_and_email(registration: Registration) -> None:
    with pytest.raises(IntegrityError):
        Registration.objects.create(
            activity=registration.activity,
            template=registration.template,
            name="Ana again",
            email="ANA@example.com",  # same email, different case
        )


@pytest.fixture
def evaluation(registration: Registration, member) -> EvaluationSubmission:
    form = FormTemplate.objects.create(
        activity=registration.activity, form_type=FormType.EVALUATION, title="Evaluation", created_by=member
    )
    return EvaluationSubmission.objects.create(template=form, registration=registration)


@pytest.fixture
def field(registration_form: FormTemplate) -> FormField:
    return FormField.objects.create(template=registration_form, field_label="Company", field_type=FieldType.SHORT_TEXT)


@pytest.mark.django_db
def test_form_answer_with_matching_parent_is_allowed(field, registration, evaluation) -> None:
    FormAnswer.objects.create(field=field, answer_source="registration", registration=registration)
    FormAnswer.objects.create(field=field, answer_source="evaluation", evaluation_submission=evaluation)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "source, use_registration, use_evaluation",
    [
        ("registration", False, False),  # no parent
        ("registration", True, True),  # both parents
        ("registration", False, True),  # parent does not match source
        ("evaluation", True, False),  # parent does not match source
    ],
)
def test_form_answer_needs_exactly_one_matching_parent(
    field, registration, evaluation, source, use_registration, use_evaluation
) -> None:
    with pytest.raises(IntegrityError):
        FormAnswer.objects.create(
            field=field,
            answer_source=source,
            registration=registration if use_registration else None,
            evaluation_submission=evaluation if use_evaluation else None,
        )
