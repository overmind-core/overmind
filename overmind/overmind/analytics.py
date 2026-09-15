"""Anonymous SDK/CLI usage analytics (PostHog) — not customer OTLP traces.

Opt out: ``OVERMIND_ANALYTICS_ENABLED=false``, ``DO_NOT_TRACK=1``, or ``CI`` set.

Identity: events start on a stable anonymous id under ``~/.overmind/analytics_id``.
When an API key is available, :func:`ensure_identified` calls ``GET /api/auth/me/``
once per key fingerprint, sends PostHog ``$identify`` with ``$anon_distinct_id``
so history merges, then caches the result so later processes skip the network.

Events ride the posthog client's background queue, drained by its own atexit
hook; :func:`track_cli_invocation` flushes explicitly so a short-lived CLI
process does not drop its one event.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

import requests
from posthog import Posthog

from overmind import __version__ as sdk_version
from overmind import config

# Write-only Console PostHog project key (same as frontend/src/analytics.ts).
_POSTHOG_API_KEY = "phc_XrIVhixaz5sOqrdzpRwwqlvKXilmcy3PWPgdk0pemZa"
_DEFAULT_HOST = "https://v.overmindlab.ai"
_TIMEOUT_S = 2.0
_ANALYTICS_ID_PATH = Path.home() / ".overmind" / "analytics_id"
_IDENTITY_CACHE_PATH = Path.home() / ".overmind" / "analytics_identity"

_SECRET_FLAGS = frozenset({
    "--api-key",
    "--token",
    "--password",
    "--secret",
    "--authorization",
    "-k",  # common short form for keys in some CLIs
})

# Caller-supplied property names never forwarded to PostHog.
_BLOCKED_PROP_KEYS = frozenset({
    "api_key",
    "api-key",
    "token",
    "password",
    "secret",
    "authorization",
    "prompt",
    "messages",
    "content",
    "body",
    "email",
})

_client: Posthog | None = None
_client_lock = threading.Lock()

_identify_lock = threading.Lock()
_identify_attempted = False
_identified_distinct_id: str | None = None


def enabled() -> bool:
    if os.environ.get("CI"):
        return False
    if os.environ.get("DO_NOT_TRACK", "").strip().lower() in ("1", "true", "yes"):
        return False
    flag = os.environ.get("OVERMIND_ANALYTICS_ENABLED", "true").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _get_client() -> Posthog:
    global _client
    with _client_lock:
        if _client is None:
            _client = Posthog(
                _POSTHOG_API_KEY,
                host=_DEFAULT_HOST,
                timeout=_TIMEOUT_S,
                max_retries=1,
                disable_geoip=False,
            )
            # After construction: Posthog.__init__ resets its logger to WARNING.
            # Analytics must never write to the host app's console.
            logging.getLogger("posthog").setLevel(logging.CRITICAL)
        return _client


def flush(timeout: float = 3.0) -> None:
    """Drain queued events; call before a short-lived process exits."""
    if _client is not None:
        with contextlib.suppress(Exception):
            _client.flush(timeout_seconds=timeout)


# --- Identity ---


def _anon_id() -> str:
    try:
        path = _ANALYTICS_ID_PATH
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        new_id = str(uuid.uuid4())
        path.write_text(new_id + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        return new_id
    except OSError:
        return str(uuid.uuid4())


def _distinct_id() -> str:
    if _identified_distinct_id:
        return _identified_distinct_id
    cached = _load_identity_cache()
    if cached and cached.get("distinct_id"):
        return str(cached["distinct_id"])
    return _anon_id()


def _load_identity_cache() -> dict[str, Any] | None:
    try:
        data = json.loads(_IDENTITY_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_identity_cache(payload: dict[str, Any]) -> None:
    try:
        path = _IDENTITY_CACHE_PATH
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    except OSError:
        pass


def _resolve_api_credentials(
    api_key: str | None = None,
    base_url: str | None = None,
) -> tuple[str, str] | None:
    """Return ``(api_key, base_url)`` from args / env / overmind.toml, or None."""
    key = (api_key or "").strip()
    url = (base_url or "").strip()
    if not key or not url:
        # Lazy: overmind.sync pulls the Typer CLI stack into library imports.
        from overmind.sync import DEFAULT_BASE_URL, resolve_api_key, resolve_api_url

        cfg = config.load(config.DEFAULT_PATH) if config.DEFAULT_PATH.exists() else None
        key = key or (resolve_api_key("", cfg) or "").strip()
        url = url or resolve_api_url("", cfg) or DEFAULT_BASE_URL
    if not key or not url:
        return None
    return key, url.rstrip("/")


def _fetch_me(api_key: str, base_url: str) -> dict[str, Any] | None:
    with contextlib.suppress(Exception):
        resp = requests.get(
            f"{base_url}/api/auth/me/",
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=_TIMEOUT_S,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data
    return None


def ensure_identified(*, api_key: str | None = None, base_url: str | None = None) -> bool:
    """Resolve the user via API key and send ``$identify`` once per key fingerprint.

    Returns True when an identify was sent or already cached for this key.
    At most one ``/api/auth/me/`` call per process (capped at ``_TIMEOUT_S``);
    later processes reuse ``~/.overmind/analytics_identity`` until the key
    changes. Never raises.
    """
    global _identified_distinct_id, _identify_attempted
    try:
        if not enabled():
            return False
        creds = _resolve_api_credentials(api_key, base_url)
        if creds is None:
            return False
        key, url = creds
        fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

        with _identify_lock:
            if _identify_attempted:
                return _identified_distinct_id is not None
            _identify_attempted = True

            cached = _load_identity_cache()
            if cached and cached.get("key_fp") == fingerprint and cached.get("distinct_id"):
                _identified_distinct_id = str(cached["distinct_id"])
                return True

            me = _fetch_me(key, url)
            if not me:
                return False

            # Console PostHog distinct_id is the Clerk id (see frontend analytics).
            distinct = str(me.get("clerk_user_id") or me.get("id") or "").strip()
            if not distinct:
                return False

            traits = {
                "sdk_surface": "python",
                "overmind_user_id": str(me.get("id") or ""),
                "plan": me.get("plan"),
                "is_guest": me.get("is_guest"),
                "project_id": _project_id(),
            }
            _get_client().capture(
                "$identify",
                distinct_id=distinct,
                properties={
                    "$anon_distinct_id": _anon_id(),
                    "$set": {k: v for k, v in traits.items() if v is not None},
                },
            )
            _identified_distinct_id = distinct
            _save_identity_cache({
                "key_fp": fingerprint,
                "distinct_id": distinct,
                "overmind_user_id": str(me.get("id") or ""),
            })
            return True
    except Exception:
        return False


# --- Event properties ---


def _project_id() -> str | None:
    with contextlib.suppress(Exception):
        if config.DEFAULT_PATH.exists():
            return config.load(config.DEFAULT_PATH).project_id or None
    return None


def _common_properties() -> dict[str, Any]:
    props: dict[str, Any] = {
        "sdk_version": sdk_version,
        "python_version": platform.python_version(),
        "os": platform.system().lower() or "unknown",
    }
    project_id = _project_id()
    if project_id:
        props["project_id"] = project_id
    return props


# --- CLI argv parsing and redaction ---


def _looks_like_secret(value: str) -> bool:
    if len(value) < 16:
        return False
    if value.lower().startswith(("om_", "sk-", "phc_", "pk_", "rk_")):
        return True
    # Long opaque tokens.
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{32,}", value))


def _argv_parts(argv: list[str] | None = None) -> list[str]:
    """Normalise argv so installs via ``python -m`` / console script look alike."""
    raw = list(argv if argv is not None else sys.argv)
    if not raw:
        return []
    if (
        raw[0].endswith(".py")
        or "python" in Path(raw[0]).name.lower()
        or Path(raw[0]).name in {"overmind", "overmind.exe"}
    ):
        return ["overmind", *raw[1:]]
    return [Path(raw[0]).name, *raw[1:]]


def _redact_parts(parts: list[str]) -> list[str]:
    out: list[str] = []
    redact_next = False
    for part in parts:
        if redact_next:
            out.append("[redacted]")
            redact_next = False
            continue
        flag = part.lower().split("=", 1)[0]
        if flag in _SECRET_FLAGS:
            if "=" in part:
                out.append(f"{part.split('=', 1)[0]}=[redacted]")
            else:
                out.append(part)
                redact_next = True
            continue
        if _looks_like_secret(part):
            out.append("[redacted]")
            continue
        out.append(part)
    return out


def _redact_argv(argv: list[str] | None = None) -> str:
    """Return the invoked CLI line with secret flag values redacted."""
    return " ".join(_redact_parts(_argv_parts(argv)))


def _looks_like_path(token: str) -> bool:
    if "/" in token or "\\" in token:
        return True
    # Positional file-like args (keep subcommands like ``upload``).
    return bool(re.search(r"\.[A-Za-z0-9]{1,8}$", token))


def cli_command_from_argv(argv: list[str] | None = None) -> str:
    """Subcommand path from argv: leading non-flag tokens that are not paths.

    ``overmind dataset upload ./data.jsonl --json`` → ``dataset upload``.
    """
    raw = list(argv if argv is not None else sys.argv)
    tokens: list[str] = []
    for part in raw[1:]:  # drop the program name
        if part.startswith("-") or _looks_like_path(part):
            break
        tokens.append(part)
    return " ".join(tokens)


def cli_flags_and_args(
    argv: list[str] | None = None,
) -> tuple[dict[str, str | bool], list[str]]:
    """Parse redacted flags and positionals after the nested command path.

    ``overmind init --ide cursor --env production`` →
    ``({"ide": "cursor", "env": "production"}, [])``.

    ``overmind dataset upload data.jsonl --json`` →
    ``({"json": True}, ["data.jsonl"])``.
    """
    raw = list(argv if argv is not None else sys.argv)
    parts = _redact_parts(_argv_parts(raw))
    if not parts:
        return {}, []
    path = cli_command_from_argv(["overmind", *raw[1:]])
    rest = parts[1 + len(path.split()) :]
    flags: dict[str, str | bool] = {}
    args: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if not tok.startswith("-"):
            args.append(tok)
            i += 1
            continue
        key = tok.lstrip("-").split("=", 1)[0]
        if "=" in tok:
            flags[key] = tok.split("=", 1)[1]
            i += 1
        elif i + 1 < len(rest) and not rest[i + 1].startswith("-"):
            flags[key] = rest[i + 1]
            i += 2
        else:
            flags[key] = True
            i += 1
    return flags, args


# --- Public event API ---


def capture(event: str, **properties: Any) -> None:
    """Fire-and-forget PostHog capture. Never raises; no-op when disabled."""
    if not enabled():
        return
    try:
        ensure_identified()
        props = {k: v for k, v in properties.items() if v is not None and k.lower() not in _BLOCKED_PROP_KEYS}
        _get_client().capture(event, distinct_id=_distinct_id(), properties={**props, **_common_properties()})
    except Exception:
        return


def track_cli_invocation(
    *,
    event: Literal["cli.invoked", "cli.completed"] = "cli.invoked",
    command: str = "",
    exit_code: int = 0,
    duration_ms: int = 0,
) -> None:
    """Emit ``cli.invoked``; ``command`` kwarg is the nested path only.

    Properties: ``command`` = full redacted argv, ``command_path`` = nested
    path, ``flags`` / ``args`` = parsed options and positionals. Flushes so
    process teardown does not drop the event. Never raises.
    """
    if not enabled():
        return
    try:
        ensure_identified()
        flags, args = cli_flags_and_args()
        props = {
            "command": _redact_argv(),
            "command_path": (command or "").strip() or cli_command_from_argv(),
            "flags": flags,
            "args": args,
            "exit_code": int(exit_code or 0),
            "duration_ms": max(0, int(duration_ms)),
            "success": int(exit_code or 0) == 0,
            "surface": "cli",
            **_common_properties(),
        }
        _get_client().capture(event, distinct_id=_distinct_id(), properties=props)
        flush()
    except Exception:
        return
