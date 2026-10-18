"""`python manage.py seed_data`: load lookup tables and the first Admin account.

Safe to run more than once. The Admin account comes from these environment
variables (set them in .env):
    SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD, SEED_ADMIN_NAME (optional)
"""

import os

from django.core.management.base import BaseCommand, CommandError

from accounts.seed import AdminAccountError, ensure_admin_account, load_lookups


class Command(BaseCommand):
    help = "Load lookup data (statuses, categories, types, roles, document types) and the Admin account."

    def handle(self, *args, **options) -> None:
        result = load_lookups()
        self.stdout.write(
            self.style.SUCCESS(f"Lookups: {result.created} created, {result.existing} already present.")
        )

        email = os.environ.get("SEED_ADMIN_EMAIL", "").strip()
        password = os.environ.get("SEED_ADMIN_PASSWORD", "")
        name = os.environ.get("SEED_ADMIN_NAME", "").strip() or "TBI Admin"
        if not email or not password:
            self.stdout.write(
                self.style.WARNING(
                    "Admin account skipped: set SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD in .env and run again."
                )
            )
            return

        try:
            admin, created = ensure_admin_account(email, password, name)
        except AdminAccountError as exc:
            raise CommandError(str(exc)) from exc

        if created:
            self.stdout.write(self.style.SUCCESS(f"Admin account created: {admin.email}"))
        else:
            self.stdout.write(f"Admin account already exists: {admin.email} (password not changed)")
