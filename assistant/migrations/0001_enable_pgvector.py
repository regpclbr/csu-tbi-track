"""Enable the pgvector extension before DOCUMENT_EMBEDDING's vector column is created.

This is the first migration of the assistant app (the app that owns the vector
column). It runs `CREATE EXTENSION IF NOT EXISTS vector`, so it is safe to run on
a database that already has the extension.

It must NOT live in `accounts`: Django's admin and auth migrations depend on the
*first* migration of the user model's app, so accounts/0001 must create TBI_MEMBER.
"""

from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        VectorExtension(),
    ]
