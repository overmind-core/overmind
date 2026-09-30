import uuid

from django.conf import settings
from django.db import models


class MCPOAuthClient(models.Model):
    id = models.CharField(primary_key=True, max_length=255)
    metadata = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class MCPOAuthGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(MCPOAuthClient, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE)
    request_hash = models.CharField(max_length=64, unique=True)
    parameters = models.JSONField()
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True)
    code_hash = models.CharField(max_length=64, null=True, unique=True)
    code_used_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MCPOAuthToken(models.Model):
    grant = models.ForeignKey(MCPOAuthGrant, on_delete=models.CASCADE)
    access_hash = models.CharField(max_length=64, unique=True)
    refresh_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list)
    access_expires_at = models.DateTimeField()
    rotated_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def scope(self):
        return {
            "scope": "account",
            "permission": [s.removeprefix("overmind:") for s in self.scopes],
        }
