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
    is_decision_model,
    normalize_model_name,
    openrouter_configured,
    pricing_slug,
)

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_KEY = "openrouter_model_catalog"
_CACHE_TTL_S = 3600
_REQUEST_TIMEOUT_S = 15

# Slugs that map 1:1 onto our curated BASE_MODELS / SUPPORTED_LLM_MODELS.
_CURATED_SLUGS = frozenset(OPENROUTER_MODEL_SLUGS.values())


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
    if is_decision_model(slug):
        return None
    if not slug or "/" not in slug:
        return None
    if not _is_text_generation(entry):
        return None
    pricing = entry.get("pricing") or {}
    return {
        "id": slug,
        "hugging_face_id": entry.get("hugging_face_id") or "",
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


def resolve_training_openrouter_slug(model_id: str) -> str | None:
    if not openrouter_configured() or "/" not in model_id:
        return None
    models, available = fetch_model_catalog()
    if not available:
        return None
    identity = model_id.strip().casefold()
    # Match the published checkpoint or the exact provider slug, never a similar name.
    matches = [
        entry["id"]
        for entry in models
        if entry["id"].casefold() == identity
        or str(entry.get("hugging_face_id") or "").casefold() == identity
    ]
    return min(matches, key=lambda slug: (":" in slug, slug)) if matches else None


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
