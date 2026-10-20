"""STARTUP: incubatee accounts. Separate from TBI_MEMBER and never a Django staff user."""

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.db.models.functions import Lower


class Startup(models.Model):
    """A startup, read from Navigatu through the StartupProvider (Step 7).

    Has its own login. `password` is hashed the same way as Django user passwords and
    is null until the startup sets one.
    """

    startup_id = models.BigAutoField(primary_key=True)
    navigatu_id = models.CharField(
        max_length=100, unique=True, null=True, blank=True, help_text="Links to the Navigatu SSO profile."
    )
    name = models.CharField(max_length=255)
    contact_number = models.CharField(max_length=30, blank=True)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=128, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    maturity_stage = models.CharField(max_length=100, null=True, blank=True)
    last_assessment_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "startup"
        ordering = ["name"]
        constraints = [
            # Startups are matched by email ignoring case, so "Ana@x.com" and "ana@x.com" are one startup.
            models.UniqueConstraint(Lower("email"), name="unique_startup_email_ignoring_case"),
        ]

    def __str__(self) -> str:
        return self.name

    def set_password(self, raw_password: str) -> None:
        """Hash and store a password (call save() afterwards)."""
        self.password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        """Return True if raw_password matches the stored hash."""
        if not self.password:
            return False
        return check_password(raw_password, self.password)
