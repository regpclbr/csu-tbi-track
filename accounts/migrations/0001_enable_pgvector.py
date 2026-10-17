"""Enable the pgvector extension before any table uses a vector column.

This is the first migration in the project. It runs
`CREATE EXTENSION IF NOT EXISTS vector`, so it is safe to run on a database
that already has the extension.
"""

from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        VectorExtension(),
    ]
