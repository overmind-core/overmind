"""OpenRouter model catalog — server-side proxy with caching.

Fetches https://openrouter.ai/api/v1/models (public; the API key header is sent
when configured). Cached for an hour so the UI never rate-limits upstream.
"""

import logging
import os

import requests
from django.core.cache import cache

from overbae.core.model_registry import (
    OPENROUTER_MODEL_SLUGS,
    normalize_model_name,
    pricing_slug,
    resolve_openrouter_slug,
)
from overbae.modal.model_registry import get_model_config_any_backend

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_KEY = "openrouter_model_catalog_v1"
_CACHE_TTL_S = 3600
_REQUEST_TIMEOUT_S = 15

# Slugs that map 1:1 onto our curated BASE_MODELS / SUPPORTED_LLM_MODELS.
_CURATED_SLUGS = frozenset(OPENROUTER_MODEL_SLUGS.values())


class CatalogUnavailableError(RuntimeError):
    """OpenRouter's model list could not be fetched, so whether it serves a model is unknown."""


def _per_million(raw: object) -> float | None:
    """OpenRouter prices are $-per-token strings; normalize to $/1M tokens."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return round(value * 1_000_000, 6)


def _is_text_generation(entry: dict) -> bool:
    """True for chat-completion models — vision LLMs (image *input*, text
    output) pass; image/audio generators and embedding/rerank models do not.

    OpenRouter ships ``architecture.modality`` as a dict, as an arrow string
    (``text+image->text``), or as a legacy ``text+image`` string; entries
    missing the field predate it and are all chat LLMs.
    """
    arch = entry.get("architecture") or {}
    modality = arch.get("modality")
    if isinstance(modality, dict):
        output = modality.get("output")
        return bool(output) and "text" in output
    elif isinstance(modality, str):
        return "text" in modality.rsplit("->", 1)[-1].split("+")
    return "text" in (entry.get("modality") or "text").rsplit("->", 1)[-1].split("+")


def _trim_entry(entry: dict) -> dict | None:
    slug = entry.get("id") or ""
    if not slug or "/" not in slug:
        return None
    if not _is_text_generation(entry):
        return None
    pricing = entry.get("pricing") or {}
    return {
        "id": slug,
        "name": entry.get("name") or slug,
        "provider": slug.split("/", 1)[0],
        "context_length": entry.get("context_length"),
        "prompt_price": _per_million(pricing.get("prompt")),
        "completion_price": _per_million(pricing.get("completion")),
        "cache_read_price": _per_million(pricing.get("input_cache_read")),
        "curated": slug in _CURATED_SLUGS,
    }


def fetch_model_catalog() -> tuple[list[dict], bool]:
    """Return ``(models, upstream_available)``; never raises. On upstream failure the
    list is empty, ``upstream_available`` is False and nothing is cached.
    """
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached, True

    headers = {}
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = requests.get(OPENROUTER_MODELS_URL, headers=headers, timeout=_REQUEST_TIMEOUT_S)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("OpenRouter model catalog fetch failed: %s", exc)
        return [], False

    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        logger.warning("OpenRouter model catalog returned unexpected payload shape")
        return [], False

    models = sorted(
        (m for m in (_trim_entry(e) for e in entries if isinstance(e, dict)) if m),
        key=lambda m: m["id"],
    )
    cache.set(_CACHE_KEY, models, _CACHE_TTL_S)
    return models, True


def resolve_bare_openrouter_slug(model_name: str) -> str | None:
    """Canonical OpenRouter slug for a bare model name (no ``provider/`` prefix),
    or ``None`` when it cannot be resolved. Curated names win; otherwise the
    cached catalog is indexed by final path segment so base models like
    ``qwen3-8b`` resolve too.
    """
    name = normalize_model_name(model_name)
    if not name or "/" in name:
        return None
    if name in OPENROUTER_MODEL_SLUGS:
        return OPENROUTER_MODEL_SLUGS[name]
    models, upstream_available = fetch_model_catalog()
    if not upstream_available:
        return None
    by_last_segment: dict[str, str] = {}
    for entry in models:
        slug = entry.get("id") or ""
        by_last_segment.setdefault(slug.rsplit("/", 1)[-1], slug)
    return by_last_segment.get(name)


def _served_slug_candidates(model_name: str) -> list[str]:
    """Slugs OpenRouter might list ``model_name`` under, most specific first.

    A training-catalog id (``Qwen/Qwen3-8B``) is usually the OpenRouter slug in
    lower case; ``openrouter_id`` in models.json pins the exceptions
    (``Qwen/Qwen2.5-7B-Instruct`` → ``qwen/qwen-2.5-7b-instruct``). The HF mirror
    id (``unsloth/…``) is never served, so it is not a candidate.
    """
    name = normalize_model_name((model_name or "").strip())
    if not name:
        return []
    candidates: list[str] = []
    cfg = get_model_config_any_backend(name)
    if cfg:
        for key in ("openrouter_id", "id"):
            if cfg.get(key):
                candidates.append(str(cfg[key]))
    if "/" in name:
        candidates.append(name.removeprefix("openrouter/"))
    else:
        qualified = resolve_openrouter_slug(name)
        if "/" in qualified:
            candidates.append(qualified)
    unique: list[str] = []
    for candidate in candidates:
        if candidate.lower() not in {c.lower() for c in unique}:
            unique.append(candidate)
    return unique


def resolve_served_slug(model_name: str) -> str | None:
    """The slug OpenRouter actually serves for ``model_name``, or ``None`` when it
    serves nothing matching. Curated names resolve without a fetch; everything
    else is matched case-insensitively against the live catalog, and a bare
    name also matches on its final path segment (``qwen3-8b`` → ``qwen/qwen3-8b``).

    Raises :class:`CatalogUnavailableError` when the catalog cannot be fetched and the
    name is not curated — the caller decides whether to wait or give up; ``None``
    always means "checked and absent".
    """
    name = normalize_model_name((model_name or "").strip())
    if not name:
        return None
    if name in OPENROUTER_MODEL_SLUGS:
        return OPENROUTER_MODEL_SLUGS[name]
    candidates = _served_slug_candidates(name)
    models, upstream_available = fetch_model_catalog()
    if not upstream_available:
        raise CatalogUnavailableError(f"OpenRouter catalog unreachable while resolving {name!r}")
    by_lower: dict[str, str] = {}
    by_last_segment: dict[str, str] = {}
    for entry in models:
        slug = entry.get("id") or ""
        by_lower.setdefault(slug.lower(), slug)
        by_last_segment.setdefault(slug.rsplit("/", 1)[-1].lower(), slug)
    for candidate in candidates:
        hit = by_lower.get(candidate.lower())
        if hit:
            return hit
    if "/" not in name:
        return by_last_segment.get(name.lower())
    return None


def estimate_cost(
    model_name: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cached_tokens: int | None = None,
) -> float | None:
    """USD cost from OpenRouter list pricing. ``None`` — an honest absence, never a
    fabricated ``0`` — when the model, the catalog or the price is missing. Catalog
    prices are $/1M tokens.

    ``cached_tokens`` is the share of ``prompt_tokens`` served from cache. It bills at
    the provider's cache-read rate, an order of magnitude under fresh input; charging
    it as fresh input overstates a long agent conversation several times over.
    """
    slug = pricing_slug(model_name)
    if slug is None:
        return None
    models, upstream_available = fetch_model_catalog()
    if not upstream_available:
        return None
    entry = next((m for m in models if m["id"] == slug), None)
    if entry is None:
        return None
    prompt_price = entry.get("prompt_price")
    completion_price = entry.get("completion_price")
    if prompt_price is None and completion_price is None:
        return None
    cached = min(max(cached_tokens or 0, 0), prompt_tokens or 0)
    fresh = (prompt_tokens or 0) - cached
    # Without a published cache rate the cached share bills as fresh input.
    cache_price = entry.get("cache_read_price")
    if cache_price is None:
        cache_price = prompt_price
    cost = (
        fresh * (prompt_price or 0.0)
        + cached * (cache_price or 0.0)
        + (completion_tokens or 0) * (completion_price or 0.0)
    ) / 1_000_000
    return round(cost, 6)


def is_model_available(model_name: str) -> bool:
    """True when ``model_name`` resolves to a slug OpenRouter actually serves — checked
    at run creation rather than failing per-sample at execution.
    """
    slug = pricing_slug(model_name)
    if slug is None:
        return False
    models, upstream_available = fetch_model_catalog()
    if not upstream_available:
        return slug in _CURATED_SLUGS
    return any(m["id"] == slug for m in models)
