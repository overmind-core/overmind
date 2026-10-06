"""Talking to the Overmind API from the CLI: one credential policy, one way to
read a response, one poller and one safe download.

Command modules keep their own exception types; every helper that can fail
takes the ``error`` class to raise so a caller's ``except`` clause stays exact.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from overmind.config import DEFAULT_PATH, Config, load

DEFAULT_BASE_URL = "https://api.overmindlab.ai"
MISSING_API_KEY = "Missing API key. Pass --api-key or set OVERMIND_API_KEY."
MISSING_PROJECT_ID = "Missing project-id. Pass --project-id or add project-id to overmind.toml."

_WINDOWS_RESERVED_BASENAMES = frozenset({
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
})


class ApiError(Exception):
    """A concise local or server-side failure talking to the Overmind API."""


def resolve_api_key(cli_key: str, config: Config | None) -> str:
    """Flag, then the key saved for this project, then ``OVERMIND_API_KEY``, then
    an inline ``api-key`` (``env:NAME`` reads that variable).

    The saved project key outranks the environment so a stale account key left
    in the shell never shadows the credential ``overmind sync`` minted.
    """
    if cli_key:
        return cli_key
    if config and config.project_id and config.api_key:
        return config.api_key
    env_key = os.environ.get("OVERMIND_API_KEY", "")
    if env_key:
        return env_key
    toml_key = (config.api_key if config else "") or ""
    if toml_key.startswith("env:"):
        return os.environ.get(toml_key[4:], "")
    return toml_key


def resolve_api_url(cli_url: str, config: Config | None) -> str:
    """Flag, then ``OVERMIND_API_URL``, then ``base-url``, then production."""
    url = cli_url or os.environ.get("OVERMIND_API_URL") or (config.base_url if config else "") or DEFAULT_BASE_URL
    return url.rstrip("/")


@dataclass(frozen=True)
class Connection:
    api_key: str
    base_url: str
    project_id: str
    config: Config

    @classmethod
    def resolve(
        cls,
        path: Path = DEFAULT_PATH,
        *,
        api_key: str = "",
        api_url: str = "",
        project_id: str = "",
        require_project: bool = False,
        error: type[Exception] = ApiError,
    ) -> Connection:
        config = load(path) if path.exists() else Config()
        key = resolve_api_key(api_key, config)
        if not key:
            raise error(MISSING_API_KEY)
        project = project_id.strip() or config.project_id.strip()
        if require_project and not project:
            raise error(MISSING_PROJECT_ID)
        return cls(api_key=key, base_url=resolve_api_url(api_url, config), project_id=project, config=config)

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"


def open_session(
    api_key: str,
    *,
    json_body: bool = True,
    session: requests.Session | None = None,
) -> requests.Session:
    session = session or requests.Session()
    session.headers.update({"X-Api-Key": api_key})
    if json_body:
        session.headers.update({"Content-Type": "application/json"})
    return session


def response_detail(response: requests.Response) -> str:
    """The server's own reason: ``error.message``, then ``detail``, then the body."""
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("detail"):
            return str(payload["detail"])[:400]
    text = str(getattr(response, "text", "") or "").strip()
    return text[:400] or "request failed"


def raise_for_status(response: requests.Response, *, error: type[Exception] = ApiError) -> None:
    if not response.ok:
        raise error(f"HTTP {response.status_code}: {response_detail(response)}")


def read_json(response: requests.Response, operation: str, *, error: type[Exception] = ApiError) -> dict[str, Any]:
    raise_for_status(response, error=error)
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise error(f"{operation} returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise error(f"{operation} returned invalid JSON.")
    return payload


def poll(
    fetch: Callable[[], dict[str, Any]],
    *,
    settled: Callable[[dict[str, Any]], bool],
    deadline: float,
    interval: float,
    timed_out: Callable[[dict[str, Any]], Exception],
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Fetch until ``settled``; ``settled`` raises for a terminal failure.

    ``sleep`` and ``clock`` default to the ``time`` module at call time, so a
    test that patches ``time.sleep`` also speeds this loop up.
    """
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    while True:
        value = fetch()
        if settled(value):
            return value
        if clock() >= deadline:
            raise timed_out(value)
        sleep(interval)


def safe_filename(name: object, *, fallback: str) -> str:
    """A server-suggested filename reduced to a plain basename that is legal on
    every platform; ``fallback`` when nothing usable is left."""
    candidate = name if isinstance(name, str) else ""
    basename = Path(candidate.replace("\\", "/")).name
    basename = (
        ""
        .join("_" if ord(char) < 32 or ord(char) == 127 or char in '<>:"|?*' else char for char in basename)
        .strip()
        .rstrip(" .")
    )
    if basename.partition(".")[0].upper() in _WINDOWS_RESERVED_BASENAMES:
        basename = f"_{basename}"
    return basename if basename not in {"", ".", ".."} else fallback


def stream_to_new_file(
    response: requests.Response,
    destination: Path,
    *,
    chunk_size: int,
    error: type[Exception],
    describe_stream_error: Callable[[requests.RequestException], str],
    label: str,
    expected_size: int | None = None,
) -> int:
    """Write ``response`` to a file that must not exist yet; delete it on any failure."""
    if destination.exists():
        raise error(f"Output path already exists: {destination}")
    created = False
    written = 0
    try:
        with destination.open("xb") as sink:
            created = True
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    sink.write(chunk)
                    written += len(chunk)
        if expected_size is not None and written != expected_size:
            raise error(f"{label} size mismatch: expected {expected_size} bytes, received {written}.")
    except BaseException as exc:
        if created:
            with suppress(OSError):
                destination.unlink()
        # RequestException subclasses OSError, so it is matched first.
        if isinstance(exc, requests.RequestException):
            raise error(describe_stream_error(exc)) from exc
        if isinstance(exc, OSError):
            raise error(f"Cannot write {label} to {destination}: {exc.strerror or exc}") from exc
        raise
    return written


def close_quietly(resource: object) -> None:
    close = getattr(resource, "close", None)
    if close is not None:
        close()
