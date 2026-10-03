import uuid

from django.conf import settings
from django.db import models


class ChatGPTAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client_id = models.CharField(max_length=255, unique=True)
    subject = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    credentials = models.TextField(blank=True)
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField(null=True)


class WorkshopPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    funding_source = models.CharField(
        max_length=16,
        choices=[("platform", "Platform"), ("chatgpt", "ChatGPT")],
        default="platform",
    )
    account = models.ForeignKey(ChatGPTAccount, on_delete=models.SET_NULL, null=True)
    model = models.CharField(max_length=255, blank=True)


class ChatGPTAuthorization(models.Model):
    state_hash = models.CharField(primary_key=True, max_length=64)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True)
    account = models.ForeignKey(ChatGPTAccount, on_delete=models.CASCADE, null=True)
    client_id = models.CharField(max_length=255, blank=True)
    retried = models.BooleanField(default=False)
    browser_hash = models.CharField(max_length=64)
    nonce = models.CharField(max_length=128)
    verifier = models.TextField()
    redirect_uri = models.URLField()
    expires_at = models.DateTimeField()


class ChatGPTInstallation(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    host_id = models.UUIDField(default=uuid.uuid4, editable=False)


class ChatGPTLoginTicket(models.Model):
    connection = models.TextField(blank=True)
    password_attempts = models.PositiveSmallIntegerField(default=0)
    browser_hash = models.CharField(primary_key=True, max_length=64)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
