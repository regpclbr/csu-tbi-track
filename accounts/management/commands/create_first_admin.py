"""Create the first System Administrator of a clean database (the hosted review version, docs/DEPLOY.md).

    docker compose ... exec web python manage.py create_first_admin

Asks for the email, the name and the password (typed twice, never shown and never printed). Refused once an active
System Administrator exists: everybody else is invited from Admin Console → Members (ASSUMPTIONS 395).
--email / --name skip those two questions; the password is always typed.
"""

import getpass

from django.core.management.base import BaseCommand, CommandError

from accounts.services import MemberManagementError, create_first_admin


class Command(BaseCommand):
    help = "Create the first System Administrator (asks for email, name and password). Refused when one already exists."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--email", help="The administrator's email (asked when left out).")
        parser.add_argument("--name", help="The administrator's full name (asked when left out).")

    # Small wrappers so the tests can answer the questions.
    def ask(self, prompt: str) -> str:
        return input(prompt)

    def ask_password(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    def handle(self, *args, **options) -> None:
        try:
            email = (options.get("email") or self.ask("Email: ")).strip()
            name = (options.get("name") or self.ask("Full name: ")).strip()
            password = self.ask_password("Password: ")
            again = self.ask_password("Password (again): ")
        except (EOFError, KeyboardInterrupt):
            raise CommandError("Cancelled; nothing was created. Run it in an interactive terminal (docker compose exec).") from None
        if password != again:
            raise CommandError("The two passwords do not match; nothing was created.")
        try:
            member = create_first_admin(email=email, name=name, password=password)
        except MemberManagementError as error:
            raise CommandError(f"{error} Nothing was created.") from None
        self.stdout.write(self.style.SUCCESS(
            f"System Administrator created: {member.name} <{member.email}>. Sign in at the site's /accounts/login/ page."
        ))
