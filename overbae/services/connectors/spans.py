"""Credential-scoped span/trace ID helpers shared by all connector adapters."""

from __future__ import annotations

import hashlib


def span_id_for(credential_id: str, observation_id: str) -> str:
    return hashlib.sha256(f"{credential_id}:{observation_id}".encode()).hexdigest()[:16]


def trace_id_for(credential_id: str, trace_id: str) -> str:
    return hashlib.sha256(f"{credential_id}:trace:{trace_id}".encode()).hexdigest()[:32]
