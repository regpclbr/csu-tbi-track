"""Smoke tests for the Step 0 project setup."""

import pytest
from django.conf import settings
from django.db import connection


@pytest.mark.django_db
def test_pgvector_extension_is_enabled() -> None:
    """The first migration installs the `vector` extension in the test database."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        assert cursor.fetchone() is not None


def test_embedding_dimensions_constant() -> None:
    """The embedding size lives in one settings constant and matches the ERD."""
    assert settings.EMBEDDING_DIMENSIONS == 768
