import hashlib
import secrets
import uuid

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


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
    projects_limit = models.IntegerField(
        null=True,
        blank=True,
        default=5,
        help_text="Max project memberships; null = unlimited (Pro).",
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


def account_scope(*, permission: list[str] | None = None) -> dict:
    return {"scope": "account", "permission": list(permission or ["read", "write"])}


def project_scope(project_id, *, permission: list[str] | None = None) -> dict:
    return {
        "scope": "project",
        "resourceIds": [str(project_id)],
        "permission": list(permission or ["read", "write"]),
    }


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
    ) -> tuple[str, "APIToken"]:
        raw = cls.generate_raw_key(prefix=prefix_str)
        prefix = raw[:12]
        key_hash = cls.hash_raw_key(raw)
        instance = cls.objects.create(
            user=user,
            name=name.strip(),
            prefix=prefix,
            token_hash=key_hash,
            project=project,
            scope=project_scope(project.pk, permission=permission)
            if project is not None
            else account_scope(permission=permission),
        )
        return raw, instance
