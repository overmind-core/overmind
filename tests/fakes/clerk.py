from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
_KID = "ins_fake"
_ids = itertools.count(1)


def _json(body: Any, status: int = 200) -> tuple[int, dict, bytes]:
    return status, {"content-type": "application/json"}, json.dumps(body).encode()


@dataclass
class ClerkAPI:
    host: str = "https://api.clerk.com"
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    invitations: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)
    down: bool = False

    def user(self, email: str) -> str:
        user_id = f"user_fake{next(_ids)}"
        email_id = f"idn_fake{next(_ids)}"
        self.users[user_id] = {
            "object": "user",
            "id": user_id,
            "primary_email_address_id": email_id,
            "email_addresses": [
                {
                    "object": "email_address",
                    "id": email_id,
                    "email_address": email,
                    "reserved": False,
                    "verification": None,
                    "linked_to": [],
                    "created_at": 0,
                    "updated_at": 0,
                }
            ],
            "external_id": None,
            "primary_phone_number_id": None,
            "primary_web3_wallet_id": None,
            "username": None,
            "first_name": None,
            "last_name": None,
            "has_image": False,
            "public_metadata": {},
            "phone_numbers": [],
            "web3_wallets": [],
            "passkeys": [],
            "external_accounts": [],
            "saml_accounts": [],
            "enterprise_accounts": [],
            "password_enabled": False,
            "two_factor_enabled": False,
            "totp_enabled": False,
            "backup_code_enabled": False,
            "mfa_enabled_at": None,
            "mfa_disabled_at": None,
            "last_sign_in_at": None,
            "banned": False,
            "locked": False,
            "lockout_expires_in_seconds": None,
            "verification_attempts_remaining": None,
            "created_at": 0,
            "updated_at": 0,
            "last_active_at": None,
            "legal_accepted_at": None,
            "create_organization_enabled": True,
            "delete_self_enabled": True,
        }
        return user_id

    def token(self, user_id: str, *, party: str = "http://localhost:5173") -> str:
        now = int(time.time())
        claims = {"sub": user_id, "azp": party, "iat": now, "nbf": now, "exp": now + 600}
        return jwt.encode(claims, _PEM, algorithm="RS256", headers={"kid": _KID})

    def _jwks(self) -> dict[str, Any]:
        key = json.loads(RSAAlgorithm.to_jwk(_KEY.public_key()))
        return {"keys": [{**key, "kid": _KID, "use": "sig", "alg": "RS256"}]}

    def invite(self, email: str) -> dict[str, Any]:
        invitation = {
            "object": "invitation",
            "id": f"inv_fake{next(_ids)}",
            "email_address": email,
            "public_metadata": {},
            "status": "pending",
            "created_at": 0,
            "updated_at": 0,
        }
        self.invitations[invitation["id"]] = invitation
        return invitation

    def handle(self, method: str, url: str, body, headers=None) -> tuple[int, dict, bytes] | None:
        if not url.startswith(self.host):
            return None
        path = urlparse(url).path
        self.requests.append(f"{method} {path}")
        if self.down:
            return _json({"errors": [{"message": "fake outage"}]}, status=503)
        if path == "/v1/jwks":
            return _json(self._jwks())
        if method == "GET" and path.startswith("/v1/users/"):
            user = self.users.get(path.rsplit("/", 1)[-1])
            return _json(user) if user else _json({"errors": [{"code": "not_found"}]}, 404)
        if method == "POST" and path == "/v1/invitations":
            payload = json.loads(body or b"{}")
            return _json(self.invite(payload["email_address"]))
        if method == "POST" and path.endswith("/revoke"):
            invitation = self.invitations.get(path.split("/")[3])
            if not invitation:
                return _json({"errors": [{"code": "not_found"}]}, 404)
            invitation["status"] = "revoked"
            return _json({**invitation, "revoked": True})
        return _json({"errors": [{"message": f"not found: {path}"}]}, 404)
