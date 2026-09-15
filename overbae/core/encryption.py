"""Fernet symmetric encryption for sensitive model fields.

The key comes from ``FIELD_ENCRYPTION_KEY``. Without it one is derived from
``SECRET_KEY`` so development boxes need no config, so production MUST set a
real one — generate it with ``Fernet.generate_key()``.
"""

from __future__ import annotations

import base64
import hashlib
import os

from django.conf import settings


def _fernet():
    from cryptography.fernet import Fernet

    raw = os.environ.get("FIELD_ENCRYPTION_KEY")
    if raw:
        key = raw.encode() if isinstance(raw, str) else raw
    else:
        # Deterministic fallback keeps local development and tests hermetic.
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode()).decode()
