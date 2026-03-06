"""
LLM response caching via a custom httpx transport.

CachingTransport wraps a real transport: on each request it checks the
on-disk cache first and returns the stored response on hit.  On miss it
forwards the request to the real transport and saves a successful (200)
response so subsequent runs are served from cache.

Cache key = SHA-256 of (url_path, request body).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LLMCache:
    """File-based LLM response cache keyed by request content hash."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_path(self, provider: str, key: str) -> Path:
        subdir = self.cache_dir / provider
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{key}.json"

    @staticmethod
    def make_cache_key(url_path: str, body: bytes) -> str:
        h = hashlib.sha256()
        h.update(url_path.encode())
        h.update(body)
        return h.hexdigest()[:24]

    def get(self, provider: str, key: str) -> dict | None:
        path = self._key_path(provider, key)
        if path.exists():
            return json.loads(path.read_text())
        return None

    def put(self, provider: str, key: str, data: dict):
        path = self._key_path(provider, key)
        path.write_text(json.dumps(data, indent=2, default=str))

    def has(self, provider: str, key: str) -> bool:
        return self._key_path(provider, key).exists()


_STRIP_HEADERS = {"content-encoding", "transfer-encoding", "content-length"}


def _response_to_dict(response: httpx.Response) -> dict[str, Any]:
    """Serialize an httpx.Response to a JSON-safe dict for caching."""
    headers = {
        k: v for k, v in response.headers.items() if k.lower() not in _STRIP_HEADERS
    }
    return {
        "status_code": response.status_code,
        "headers": headers,
        "body": response.text,
    }


def _dict_to_response(data: dict[str, Any]) -> httpx.Response:
    """Reconstruct an httpx.Response from a cached dict."""
    headers = {
        k: v
        for k, v in data.get("headers", {}).items()
        if k.lower() not in _STRIP_HEADERS
    }
    return httpx.Response(
        status_code=data["status_code"],
        headers=headers,
        text=data["body"],
    )


class CachingTransport(httpx.BaseTransport):
    """First run: forwards to real transport, saves response to cache."""

    def __init__(
        self, real_transport: httpx.BaseTransport, cache: LLMCache, provider: str
    ):
        self._real = real_transport
        self._cache = cache
        self._provider = provider

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content or b""
        key = LLMCache.make_cache_key(request.url.path, body)

        cached = self._cache.get(self._provider, key)
        if cached:
            logger.debug("Cache HIT for %s %s", self._provider, key)
            return _dict_to_response(cached)

        logger.info("Cache MISS for %s %s -- calling real API", self._provider, key)
        response = self._real.handle_request(request)

        response.read()
        if response.status_code == 200:
            self._cache.put(self._provider, key, _response_to_dict(response))

        return response
