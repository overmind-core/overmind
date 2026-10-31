import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("The email must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        # Lazy import: billing_ledger imports User.
        from overbae.services.billing_ledger import grant_free_credits

        if not user.is_guest:
            grant_free_credits(user)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class SignOnMethod(models.TextChoices):
    PASSWORD = "password"
    GOOGLE = "google"
    SAML = "saml"


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(default=True)
    avatar_url = models.URLField(max_length=1024, blank=True, default="")
    timezone = models.CharField(max_length=64, blank=True, default="UTC")
    sign_on_method = models.CharField(
        max_length=20,
        choices=SignOnMethod.choices,
        default=SignOnMethod.PASSWORD,
    )
    clerk_user_id = models.CharField(max_length=255, blank=False, null=False)
    is_guest = models.BooleanField(default=False, db_index=True)
    stripe_customer_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Stable Stripe customer id; one per user.",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self):
        return self.email


class IntegrationType(models.TextChoices):
    SDK = "sdk", "SDK"


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    integration_type = models.CharField(
        max_length=10,
        choices=IntegrationType.choices,
        default=IntegrationType.SDK,
    )
    is_active = models.BooleanField(default=True)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("slug",)]

    def __str__(self):
        return self.name


class ProjectInvite(models.Model):
    """A pending invitation for an email with no console account yet. Claimed
    (converted to a membership) when the invitee first signs in.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="invites")
    email = models.EmailField()
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_project_invites",
    )
    clerk_invitation_id = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("project", "email")]
        ordering = ["-created_at"]


class ProjectMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "project")]
        ordering = ["-created_at"]


class UserOnboarding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="onboarding",
    )
    step = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=64, blank=True, default="")
    priorities = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Onboarding({self.user.email}, step={self.step})"


# Same choices as the Console "Expires after" control. 0 means the key does not expire.
API_KEY_EXPIRY_DAYS = (0, 30, 90, 180, 365)


def account_scope(*, permission: list[str] | None = None) -> dict:
    return {"scope": "account", "permission": list(permission or ["read", "write"])}


def project_scope(project_id, *, permission: list[str] | None = None) -> dict:
    return {
        "scope": "project",
        "resourceIds": [str(project_id)],
        "permission": list(permission or ["read", "write"]),
    }


def delegation_denial(scope: dict, project_id, permission: list[str] | None = None) -> str | None:
    """Why a key holding ``scope`` cannot create a key for ``project_id`` (None: account)."""
    if scope.get("scope") != "account" and (
        project_id is None or str(project_id) not in scope.get("resourceIds", [])
    ):
        return "A project API key can only create a key for its own project."
    if not set(permission or ["read", "write"]) <= set(scope.get("permission", [])):
        return "An API key can only create a key with permissions it holds."
    return None


class APIToken(models.Model):
    """Only the SHA-256 hash is stored; the plaintext key is returned once.

    ``scope`` is account (all memberships) or project (one id in resourceIds).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_tokens",
    )
    project = models.ForeignKey(
        "overbae.Project",
        on_delete=models.CASCADE,
        related_name="api_tokens",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    token_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=12, db_index=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    allowed_ips = models.JSONField(default=list, blank=True)
    rate_limit = models.JSONField(default=dict, blank=True)
    scope = models.JSONField(default=account_scope)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.prefix}… ({self.user_id})"

    @property
    def is_account_scoped(self) -> bool:
        return (self.scope or {}).get("scope") == "account"

    def allowed_project_ids(self):
        if self.is_account_scoped:
            return list(
                ProjectMembership.objects.filter(user_id=self.user_id).values_list(
                    "project_id", flat=True
                )
            )
        raw = (self.scope or {}).get("resourceIds") or []
        if raw:
            return raw
        return [self.project_id] if self.project_id else []

    @staticmethod
    def hash_raw_key(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def generate_raw_key(cls, prefix: str = "ovr_") -> str:
        random_part = secrets.token_urlsafe(32)
        return f"{prefix}{random_part}"

    @classmethod
    def create_for_user(
        cls,
        user,
        name: str = "",
        project=None,
        prefix_str: str = "ovr_",
        *,
        permission: list[str] | None = None,
        description: str = "",
        expires_in_days: int | None = None,
    ) -> tuple[str, "APIToken"]:
        if expires_in_days not in {None, *API_KEY_EXPIRY_DAYS}:
            raise ValueError("expires_in_days is not a Console expiry choice")
        raw = cls.generate_raw_key(prefix=prefix_str)
        prefix = raw[:12]
        key_hash = cls.hash_raw_key(raw)
        instance = cls.objects.create(
            user=user,
            name=name.strip(),
            description=description.strip(),
            prefix=prefix,
            token_hash=key_hash,
            project=project,
            expires_at=(
                timezone.now() + timedelta(days=expires_in_days) if expires_in_days else None
            ),
            scope=project_scope(project.pk, permission=permission)
            if project is not None
            else account_scope(permission=permission),
        )
        return raw, instance


class ProjectHistory(models.Model):
    """Receipts retained when a project-level workflow is retired."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="history")
    kind = models.CharField(max_length=32)
    reference = models.CharField(max_length=128)
    payload = models.JSONField(default=dict)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("project", "kind", "reference"), name="unique_project_history"
            )
        ]
