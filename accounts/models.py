"""ROLE and TBI_MEMBER (the custom Django user model)."""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower


class Role(models.Model):
    """A staff role. Never deleted. Permissions come only from the three flags, never from the name.

    SYSTEM_ADMINISTRATOR is the role create_superuser and seed_data give the system administrator: it manages users
    and does not create activities (separation of duties, ASSUMPTIONS 85 and 339).
    """

    SYSTEM_ADMINISTRATOR = "System Administrator"

    role_id = models.BigAutoField(primary_key=True)
    role_name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    can_create_activity = models.BooleanField(default=False)
    can_manage_users = models.BooleanField(default=False)
    # Approve or return documents (ASSUMPTIONS 344): True only for Director by default; the System Administrator
    # turns it on or off in the Admin Console roles screen.
    can_approve_documents = models.BooleanField(default=False, db_default=False)
    # Approve certificates (ASSUMPTIONS 357): True only for Director by default; switched like can_approve_documents.
    can_approve_certificates = models.BooleanField(default=False, db_default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "role"
        ordering = ["role_name"]

    def __str__(self) -> str:
        return self.role_name


class TBIMemberManager(BaseUserManager):
    """Creates staff accounts. Email is the login."""

    def get_by_natural_key(self, email: str) -> "TBIMember":
        """Find a member for login; email is matched ignoring upper/lower case."""
        return self.get(email__iexact=email)

    def create_user(
        self, email: str, name: str, role: Role, password: str | None = None, **extra_fields
    ) -> TBIMember:
        """Create a member who must change their password at first sign-in."""
        if not email:
            raise ValueError("Email is required.")
        member = self.model(email=self.normalize_email(email).lower(), name=name, role=role, **extra_fields)
        member.set_password(password)  # None gives an unusable password
        member.save(using=self._db)
        return member

    def create_superuser(self, email: str, name: str, password: str | None = None, **extra_fields) -> TBIMember:
        """Create a System Administrator account (used by `manage.py createsuperuser`).

        The System Administrator role must already exist (it is loaded by the seed command in Step 2).
        """
        try:
            role = Role.objects.get(role_name=Role.SYSTEM_ADMINISTRATOR)
        except Role.DoesNotExist as exc:
            raise ValueError(f"Load the roles first: the '{Role.SYSTEM_ADMINISTRATOR}' role does not exist.") from exc
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("must_change_password", False)
        return self.create_user(email, name, role, password, **extra_fields)


class TBIMember(AbstractBaseUser):
    """A TBI staff member and the Django user model (AUTH_USER_MODEL).

    AbstractBaseUser adds `password` and `last_login`. We do not use PermissionsMixin
    (it would add Django's groups and permissions tables, which are not in the ERD).
    Access is decided by the member's role instead.
    """

    member_id = models.BigAutoField(primary_key=True)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="members")
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    must_change_password = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, help_text="Admin deactivates accounts instead of deleting them.")
    is_superuser = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TBIMemberManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        db_table = "tbi_member"
        ordering = ["name"]
        constraints = [
            # Login matches the email ignoring case, so "Ana@x.com" and "ana@x.com" must be one member
            # (migration accounts/0002 checks existing rows first).
            models.UniqueConstraint(Lower("email"), name="unique_member_email_ignoring_case"),
        ]

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"

    @property
    def is_staff(self) -> bool:
        """Only members whose role can manage users (System Administrator) may open the Django admin site."""
        return self.role.can_manage_users

    def has_perm(self, perm: str, obj: object | None = None) -> bool:
        """Admin-site permission check: active members whose role can manage users. Screens that change activity
        data also need can_create_activity (config.admin_access, ASSUMPTIONS 339)."""
        return self.is_active and self.is_staff

    def has_module_perms(self, app_label: str) -> bool:
        """Admin-site check for showing an app's models."""
        return self.is_active and self.is_staff
