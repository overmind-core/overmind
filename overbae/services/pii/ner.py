"""NER PII detection and redaction, keyed off a content-signature span cache.

Hosted Modal GLiNER is the SOLE engine — there is deliberately no local
fallback, because a weaker model would stamp the same "complete" confidence
while catching fewer entities. With Modal unconfigured a scan detects no NER
spans; the workshop's narrower regex literal layer
(``workshop.detectors._PII_LITERALS``) is then the only detection and the scan
reports ``pii_complete=False`` rather than an all-clear.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from overbae.core.utils import recurse_redact

logger = logging.getLogger(__name__)

__all__ = [
    "active_backend",
    "available",
    "cache_only",
    "configure_disk_cache",
    "delta_scan",
    "labels_for",
    "prewarm_async",
    "redact_text",
    "redact_value",
    "redact_value_cached",
    "spans_for",
    "warm",
]

# Text past this prefix is not scanned: bounds worst-case work at the cost of
# missing PII in very long rows.
_PII_MAX_SCAN_CHARS = 16_384

# {"start": int, "end": int, "label": str, "score"?: float}
Span = dict[str, Any]

_SPANS: dict[str, list[Span]] = {}  # content_signature -> spans


# The fan-out must be paid once per dataset. The baseline scan persists its
# spans to a JSON sidecar under the workshop dir, so a second process sharing
# that dir (the stdio ``score_server``, whose cwd IS the workshop dir) or a later
# re-analysis reuses them.
_DISK_CACHE_DIR: str | None = None  # <workshop_dir>/_scorecache, or None
_disk_loaded = False
_SPANS_FILE = "pii_ner_spans.json"
# ContextVars, NOT module globals: the workshop worker runs a threads pool, so
# one task's ``cache_only``/``delta_scan`` block must never suppress a
# concurrently running analysis task's baseline fan-out.
_no_fanout: ContextVar[bool] = ContextVar("pii_ner_no_fanout", default=False)
_delta_full: ContextVar[bool] = ContextVar("pii_ner_delta_full", default=False)


def configure_disk_cache(directory: str | None) -> None:
    """Point the span cache at ``<directory>/_scorecache``; ``None`` clears it.

    Idempotent and process-global. Must be set before the canonical scan for the
    baseline spans to persist.
    """
    global _DISK_CACHE_DIR, _disk_loaded
    new_dir = os.path.join(directory, "_scorecache") if directory else None
    if new_dir != _DISK_CACHE_DIR:
        _DISK_CACHE_DIR = new_dir
        _disk_loaded = False  # re-load lazily for the new location


def _spans_disk_path() -> str | None:
    return os.path.join(_DISK_CACHE_DIR, _SPANS_FILE) if _DISK_CACHE_DIR else None


def _ensure_disk_loaded() -> None:
    """Merge the on-disk span sidecar into :data:`_SPANS` once per configured dir.

    Every failure path is a silent no-op, so a bad sidecar can only forgo the
    reuse — never corrupt detection.
    """
    global _disk_loaded
    if _disk_loaded or _DISK_CACHE_DIR is None:
        return
    _disk_loaded = True  # set first: a corrupt file must not retry every call
    path = _spans_disk_path()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    loaded = 0
    for sig, spans in data.items():
        if isinstance(sig, str) and sig not in _SPANS and isinstance(spans, list):
            _SPANS[sig] = spans
            loaded += 1
    if loaded:
        logger.info("PII NER span cache: loaded %d signatures from disk (%s)", loaded, path)


def _persist_spans(new_sigs: list[str]) -> None:
    """Merge the just-scanned ``new_sigs`` spans into the on-disk sidecar.

    Only signatures scanned under the currently-configured dir are written, so a
    worker handling several workshops never leaks one dataset's spans into
    another's cache. Atomic replace; an OS error just skips persisting.
    """
    path = _spans_disk_path()
    if path is None or not new_sigs:
        return
    try:
        try:
            with open(path, encoding="utf-8") as fh:
                existing = json.load(fh)
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, ValueError):
            existing = {}
        for sig in new_sigs:
            if sig in _SPANS:
                existing[sig] = _SPANS[sig]
        os.makedirs(_DISK_CACHE_DIR, exist_ok=True)  # type: ignore[arg-type]
        fd, tmp = tempfile.mkstemp(dir=_DISK_CACHE_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(existing, fh, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError:
        return


def fanout_suppressed() -> bool:
    """True inside :func:`cache_only` / :func:`delta_scan` — no full fan-out runs."""
    return _no_fanout.get()


@contextmanager
def cache_only() -> Iterator[None]:
    """Within this block, NER never fans out — it rides the cache only.

    An uncached (mutated) row therefore contributes NO PII spans rather than
    paying a second Modal scan. The canonical baseline scan must run outside.
    Re-entrant and context-local; restores prior state.
    """
    fan_token = _no_fanout.set(True)
    delta_token = _delta_full.set(False)  # pure cache ride — no delta scan
    try:
        yield
    finally:
        _no_fanout.reset(fan_token)
        _delta_full.reset(delta_token)


@contextmanager
def delta_scan() -> Iterator[None]:
    """Cache-ride for unchanged rows + a real scan of the FULL uncached delta.

    Unlike :func:`cache_only`, every uncached (mutated) signature is still
    scanned, so a change that introduces PII is caught at the score gate instead
    of riding through as a cache miss. Re-entrant, context-local.
    """
    fan_token = _no_fanout.set(True)
    delta_token = _delta_full.set(True)
    try:
        yield
    finally:
        _no_fanout.reset(fan_token)
        _delta_full.reset(delta_token)


def available() -> bool:
    """True when the NER backend can run. False ⇒ a scan detects no NER spans."""
    return _modal_ner_available()


# Spans are filtered to this set; EMAIL/PHONE only arrive once the deployment is
# configured to emit them.
_VALID_LABELS = frozenset({"PERSON_NAME", "LOCATION", "ORG", "EMAIL", "PHONE"})

# One neutral token for EVERY entity: a per-label token like ``[PERSON_NAME]``
# reads as a name/place and is re-detected on the next scan, so the "Mask PII"
# fix would never converge. The span's ``label`` is still kept for reporting.
_REDACTION_TOKEN = "[REDACTED]"


def _strip_token_spans(text: str, spans: list[Span]) -> list[Span]:
    """Drop any span that OVERLAPS a ``[REDACTED]`` mask token occurrence.

    GLiNER non-deterministically tags the bare token, often together with a
    neighbouring word (``"the [REDACTED]"``), so without this an already-redacted
    row re-flags forever. Overlap, not exact match, to catch those token+word
    spans; the cost is a genuine entity glued to a token with no separator.
    Applied at span-production time so the cache never holds a token span.
    """
    if not text or _REDACTION_TOKEN not in text:
        return spans
    tok_len = len(_REDACTION_TOKEN)
    ranges: list[tuple[int, int]] = []
    idx = text.find(_REDACTION_TOKEN)
    while idx >= 0:
        ranges.append((idx, idx + tok_len))
        idx = text.find(_REDACTION_TOKEN, idx + tok_len)
    return [
        s for s in spans if not any(s["start"] < end and start < s["end"] for start, end in ranges)
    ]


def _resolve_overlaps(spans: list[Span]) -> list[Span]:
    """Keep non-overlapping spans (earliest start, then longest, wins), in document order."""
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: (s["start"], -(s["end"] - s["start"])))
    kept: list[Span] = []
    for s in ordered:
        if any(s["start"] < k["end"] and k["start"] < s["end"] for k in kept):
            continue
        kept.append(s)
    kept.sort(key=lambda s: s["start"])
    return kept


def _signature(text: str) -> str:
    capped = text if len(text) <= _PII_MAX_SCAN_CHARS else text[:_PII_MAX_SCAN_CHARS]
    return hashlib.sha1(capped.encode("utf-8", "replace")).hexdigest()  # noqa: S324


# Sole backend: the autoscaling GLiNER service on Modal (overbae/modal/modal_pii_ner.py).
# Every row is scanned — no sampling.

_PII_NER_BATCH_SIZE = int(os.environ.get("PII_NER_BATCH_SIZE", "40"))
# Tracks the service's cost-capped max_containers. Locked low deliberately:
# ~120–140 rows/s on a ~12×T4 fleet is the cheapest accepted config, and raising
# this adds cost without throughput.
_PII_NER_CONCURRENCY = int(os.environ.get("PII_NER_CONCURRENCY", "16"))
# Generous read timeout because the service scales to zero: the first request
# after idle pays container boot + GLiNER load. The short connect timeout still
# fails fast on an unreachable URL.
_PII_NER_TIMEOUT = float(os.environ.get("PII_NER_TIMEOUT", "120"))
_PII_NER_CONNECT_TIMEOUT = float(os.environ.get("PII_NER_CONNECT_TIMEOUT", "15"))
_PII_NER_WARMUP_TIMEOUT = float(os.environ.get("PII_NER_WARMUP_TIMEOUT", "90"))
_PII_NER_WARMUP_PINGS = int(os.environ.get("PII_NER_WARMUP_PINGS", str(_PII_NER_CONCURRENCY)))
# The service windows long texts internally, so the whole capped text is scanned.
_PII_NER_MAX_CHARS = _PII_MAX_SCAN_CHARS
_PII_NER_MAX_RETRIES = int(os.environ.get("PII_NER_MAX_RETRIES", "4"))
# Sent to the endpoint as the per-request ``threshold``. Drops the low-confidence
# zero-shot false positives (anatomy as LOCATION, stray tokens) at the cost of
# missing genuine PII the model is unsure about.
_PII_NER_MIN_SCORE_DEFAULT = 0.8


def _min_score() -> float:
    """Resolve the GLiNER confidence cutoff (env > Django setting > default)."""
    raw: Any = os.environ.get("PII_NER_MIN_SCORE")
    if raw is None:
        try:
            from django.conf import settings  # noqa: PLC0415

            raw = getattr(settings, "PII_NER_MIN_SCORE", None)
        except Exception:  # noqa: BLE001 — Django not configured ⇒ env/default only
            raw = None
    if raw is None:
        return _PII_NER_MIN_SCORE_DEFAULT
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _PII_NER_MIN_SCORE_DEFAULT


def _modal_config() -> tuple[str, str] | None:
    """Return ``(endpoint_url, token)``, or ``None`` when either is unset.

    ``None`` means no backend, so a scan detects no NER spans.
    """
    url = os.environ.get("PII_NER_ENDPOINT_URL", "")
    token = os.environ.get("PII_NER_TOKEN", "")
    if not url or not token:
        try:
            from django.conf import settings  # noqa: PLC0415

            url = url or getattr(settings, "PII_NER_ENDPOINT_URL", "")
            token = token or getattr(settings, "PII_NER_TOKEN", "")
        except Exception:  # noqa: BLE001 — Django not configured ⇒ env-only
            pass
    return (url.rstrip("/"), token) if url and token else None


def _modal_ner_available() -> bool:
    return _modal_config() is not None


def _normalize_spans(raw: Any) -> list[Span]:
    spans: list[Span] = []
    if not isinstance(raw, list):
        return spans
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if label not in _VALID_LABELS:
            continue
        try:
            start, end = int(item["start"]), int(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= start < end:
            spans.append({"start": start, "end": end, "label": label})
    return spans


def _infer_timeout(read: float) -> Any:
    """Cold-tolerant httpx timeout: short connect, long read (absorbs boot)."""
    import httpx  # noqa: PLC0415

    return httpx.Timeout(read, connect=_PII_NER_CONNECT_TIMEOUT)


def _is_cold_start_error(exc: Exception) -> bool:
    """True for the transient signals a scaled-to-zero service throws on boot.

    Timeouts and 5xx are ridden out; anything else (401 auth, 400 payload) is a
    real error not worth retrying.
    """
    import httpx  # noqa: PLC0415

    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (500, 502, 503, 504)
    return False


def _modal_infer(
    client: Any,
    endpoint: str,
    headers: dict,
    texts: list[str],
    *,
    read_timeout: float | None = None,
) -> list[list[Span]]:
    """POST one batch of texts to the Modal endpoint; return per-text spans.

    Retries cold-start signals with backoff; other errors surface immediately.
    Raises on final failure so the caller drops that batch without poisoning the
    cache.
    """
    timeout = _infer_timeout(read_timeout if read_timeout is not None else _PII_NER_TIMEOUT)
    last_exc: Exception | None = None
    for attempt in range(_PII_NER_MAX_RETRIES):
        try:
            body = {"texts": texts, "threshold": _min_score()}
            resp = client.post(endpoint, json=body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return [_normalize_spans(r) for r in results]
        except Exception as exc:  # noqa: BLE001 — retry cold-start, then surface
            last_exc = exc
            if attempt < _PII_NER_MAX_RETRIES - 1 and _is_cold_start_error(exc):
                time.sleep(1.5 * (attempt + 1))
            elif attempt < _PII_NER_MAX_RETRIES - 1 and not _is_cold_start_error(exc):
                break  # non-transient — don't burn the remaining retries
    raise last_exc if last_exc else RuntimeError("modal infer failed")


def _warmup_modal(pool: Any, client: Any, endpoint: str, headers: dict, n_pings: int) -> bool:
    """Boot the scaled-to-zero service to width BEFORE the real fan-out.

    Without this the first batches race a cold service and the whole scan crawls
    (~28 rows/s ramping vs ~120 warm). Returns True if any ping succeeded; never
    raises.
    """
    t0 = time.monotonic()

    def _ping(_i: int) -> float | None:
        try:
            _modal_infer(
                client, endpoint, headers, ["warm up"], read_timeout=_PII_NER_WARMUP_TIMEOUT
            )
            return time.monotonic() - t0
        except Exception as exc:  # noqa: BLE001 — warmup is best-effort
            logger.info("Modal PII NER warmup ping failed: %s", exc)
            return None

    latencies = [r for r in pool.map(_ping, range(max(1, n_pings))) if r is not None]
    dt = time.monotonic() - t0
    if latencies:
        logger.info(
            "Modal PII NER warmed %d/%d container ping(s) in %.1fs (slowest %.1fs) — fanning out",
            len(latencies),
            max(1, n_pings),
            dt,
            max(latencies),
        )
        return True
    logger.warning(
        "Modal PII NER warmup pings all failed in %.1fs; fan-out will absorb cold start", dt
    )
    return False


def _warm_modal(
    items: list[tuple[str, str]],
    progress_cb: Callable[[int, int], None] | None = None,
) -> int:
    """Scan uncached ``(signature, text)`` pairs via the hosted Modal NER service.

    Deduped by signature; returns the deduped row count. A failed batch leaves
    its rows with NO PII spans. The ``ThreadPoolExecutor`` is safe inside prefork
    Celery — these are I/O-bound network threads, no fork/spawn.

    ``progress_cb`` fires ``(completed_rows, total_rows)`` from worker context as
    batches complete; callers must marshal/throttle it themselves.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    cfg = _modal_config()
    if cfg is None:
        return 0
    url, token = cfg

    pending: dict[str, str] = {}
    for sig, text in items:
        if sig in _SPANS or sig in pending:
            continue
        pending[sig] = text if len(text) <= _PII_NER_MAX_CHARS else text[:_PII_NER_MAX_CHARS]
    if not pending:
        return 0

    sig_items = list(pending.items())
    total = len(sig_items)
    batches = [
        sig_items[i : i + _PII_NER_BATCH_SIZE]
        for i in range(0, len(sig_items), _PII_NER_BATCH_SIZE)
    ]
    endpoint = f"{url}/infer"
    headers = {"Authorization": f"Bearer {token}"}
    max_workers = max(1, min(_PII_NER_CONCURRENCY, len(batches)))
    logger.info(
        "Modal PII NER: scanning %d rows in %d batches (concurrency=%d)",
        total,
        len(batches),
        max_workers,
    )

    def _run(batch: list[tuple[str, str]], client: Any) -> dict[str, list[Span]]:
        try:
            per_text = _modal_infer(client, endpoint, headers, [t for _s, t in batch])
        except Exception as exc:  # noqa: BLE001 — never let detection crash a caller
            logger.warning(
                "Modal PII NER batch failed (%s); %d rows left without PII spans",
                exc,
                len(batch),
            )
            return {}
        return {
            sig: _strip_token_spans(t, spans)
            for (sig, t), spans in zip(batch, per_text, strict=False)
        }

    completed = 0
    with (
        httpx.Client(http2=False) as client,
        ThreadPoolExecutor(max_workers=max_workers) as pool,
    ):
        _warmup_modal(pool, client, endpoint, headers, min(_PII_NER_WARMUP_PINGS, len(batches)))
        futures = {pool.submit(_run, batch, client): len(batch) for batch in batches}
        for fut in as_completed(futures):
            _SPANS.update(fut.result())
            completed += futures[fut]
            if progress_cb is not None:
                try:
                    progress_cb(completed, total)
                except Exception:  # noqa: BLE001 — progress is best-effort, never fatal
                    logger.debug("PII NER progress callback failed", exc_info=True)
    # Cache empty for signatures a failed batch dropped, so the pass doesn't
    # re-issue per-row lazy calls.
    for sig, _t in sig_items:
        _SPANS.setdefault(sig, [])
    return total


def prewarm_async(n_pings: int | None = None) -> None:
    """Fire-and-forget Modal container boot so its cold start overlaps other work.

    Best-effort: a no-op with no backend configured, and never raises into or
    blocks the caller.
    """
    if not _modal_ner_available():
        return

    def _boot() -> None:
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        import httpx  # noqa: PLC0415

        try:
            cfg = _modal_config()
            if cfg is None:
                return
            url, token = cfg
            endpoint = f"{url}/infer"
            headers = {"Authorization": f"Bearer {token}"}
            pings = max(1, n_pings if n_pings is not None else _PII_NER_WARMUP_PINGS)
            with (
                httpx.Client(http2=False) as client,
                ThreadPoolExecutor(max_workers=pings) as pool,
            ):
                _warmup_modal(pool, client, endpoint, headers, pings)
        except Exception:  # noqa: BLE001 — prewarm is best-effort, never fatal
            logger.debug("Modal PII NER async prewarm failed", exc_info=True)

    threading.Thread(target=_boot, name="pii-ner-prewarm", daemon=True).start()


def active_backend() -> str:
    """Which backend a scan would use: ``modal``/``none``. Makes no network call."""
    return "modal" if _modal_ner_available() else "none"


def warm(
    items: list[tuple[str, str]],
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Batch-run NER over the uncached ``(signature, text)`` pairs.

    A no-op with no backend configured. Returns
    ``{"backend", "scanned", "elapsed_s"}``.
    """
    stats: dict[str, Any] = {"backend": "none", "scanned": 0, "elapsed_s": 0.0}
    if not items:
        return stats

    _ensure_disk_loaded()

    # Under delta_scan the uncached (mutated) signatures are still scanned, so an
    # introduced-PII change can't ride through the gate undetected.
    if _no_fanout.get():
        if not _delta_full.get():
            stats["backend"] = "cache-only"
            return stats
        items = [(sig, text) for sig, text in items if sig not in _SPANS]
        if not items:
            stats["backend"] = "cache-only"
            return stats

    before_keys = set(_SPANS.keys())
    t0 = time.monotonic()
    if _modal_ner_available():
        scanned = _warm_modal(items, progress_cb=progress_cb)
        stats.update(backend="modal", scanned=scanned, elapsed_s=time.monotonic() - t0)
        _persist_spans([sig for sig, _ in items if sig not in before_keys])
        return stats
    # No backend ⇒ uncached rows produce no NER spans; the caller's literal layer
    # is the only (incomplete) scan.
    return stats


def spans_for(sig: str, text: str | None = None) -> list[Span]:
    """NER spans for a content signature; lazily warm ``text`` on a miss.

    Under :func:`cache_only` a miss contributes no spans instead of fanning out.
    """
    _ensure_disk_loaded()
    cached = _SPANS.get(sig)
    if cached is not None:
        return cached
    if text is not None and not _no_fanout.get() and available():
        warm([(sig, text)])
        return _SPANS.get(sig, [])
    return []


def labels_for(sig: str, text: str | None = None) -> list[str]:
    seen: list[str] = []
    for s in spans_for(sig, text):
        if s["label"] not in seen:
            seen.append(s["label"])
    return seen


def _ner_spans_for_text(text: str) -> list[Span]:
    if not text or not available():
        return []
    return spans_for(_signature(text), text)


def _placeholder(label: str) -> str:  # noqa: ARG001 — uniform token, label kept on the span
    return _REDACTION_TOKEN


def _apply_spans(text: str, spans: list[Span]) -> str:
    """Mask ``spans``, right-to-left so earlier offsets stay valid."""
    out = text
    for s in sorted(spans, key=lambda x: x["start"], reverse=True):
        out = out[: s["start"]] + _placeholder(s["label"]) + out[s["end"] :]
    return out


def redact_text(text: str) -> tuple[str, list[Span]]:
    """Mask NER spans in ``text``; returns the applied spans left-to-right.

    A no-op when no NER backend is available.
    """
    if not isinstance(text, str) or not text:
        return text, []

    spans = _resolve_overlaps(_ner_spans_for_text(text))
    if not spans:
        return text, []

    return _apply_spans(text, spans), spans


def redact_value(value: Any) -> tuple[Any, bool]:
    """Recursively redact PII in a str / dict / list value."""

    def _leaf(text: str) -> tuple[str, bool]:
        new_text, _spans = redact_text(text)
        return new_text, new_text != text

    return recurse_redact(value, _leaf)


def _cached_entity_terms(signature: str, row_text: str) -> dict[str, str]:
    """Entity ``{substring: label}`` map from a row's already-cached NER spans.

    Cached spans are offsets into ``row_text``, but redaction rewrites the field
    values, so each entity's literal substring is lifted out for later matching.
    A cache miss yields no terms — never a Modal call — leaving the row
    un-redacted.
    """
    _ensure_disk_loaded()
    spans = _SPANS.get(signature)
    if not spans:
        return {}
    n = len(row_text)
    terms: dict[str, str] = {}
    for s in spans:
        start, end = s.get("start"), s.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if 0 <= start < end <= n:
            frag = row_text[start:end]
            if frag:
                terms.setdefault(frag, s["label"])
    return terms


def _word_bounded(text: str, start: int, end: int) -> bool:
    """True unless ``[start, end)`` is glued INSIDE a larger alphanumeric word.

    The zero-shot model mistags lone tokens (``"e"``, ``"he"``) as PERSON_NAME;
    without this a global find mangles unrelated words (``urine`` →
    ``urin[REDACTED]``). The boundary is only enforced on a side whose own edge
    char is alphanumeric, so entities starting/ending in punctuation still match.
    """
    if text[start].isalnum() and start > 0 and text[start - 1].isalnum():
        return False
    return not (text[end - 1].isalnum() and end < len(text) and text[end].isalnum())


def _redact_text_cached(text: str, terms: dict[str, str]) -> tuple[str, list[Span]]:
    """Redact ``text`` by locating each cached entity substring on word boundaries."""
    if not isinstance(text, str) or not text:
        return text, []
    spans: list[Span] = []
    for frag, label in terms.items():
        start = 0
        while True:
            idx = text.find(frag, start)
            if idx < 0:
                break
            if _word_bounded(text, idx, idx + len(frag)):
                spans.append({"start": idx, "end": idx + len(frag), "label": label})
            start = idx + len(frag)
    spans = _resolve_overlaps(spans)
    if not spans:
        return text, []
    return _apply_spans(text, spans), spans


def redact_value_cached(
    value: Any,
    row_text: str,
    signature: str,
    *,
    labels: set[str] | None = None,
    columns: set[str] | None = None,
) -> tuple[Any, bool]:
    """Recursively redact PII in a value, reusing the row's cached NER spans.

    Drop-in replacement for :func:`redact_value` on the apply path, guaranteeing
    no fresh Modal scan fires. ``labels``: ``None`` = all, ``set()`` = none.
    ``columns`` (dicts only): ``None`` = every leaf; unselected columns stay
    byte-identical.
    """
    terms = _cached_entity_terms(signature, row_text)
    if labels is not None:
        terms = {frag: lbl for frag, lbl in terms.items() if lbl in labels}

    def _leaf(text: str) -> tuple[str, bool]:
        new_text, _spans = _redact_text_cached(text, terms)
        return new_text, new_text != text

    if columns is not None and isinstance(value, dict):
        new_row = dict(value)
        changed = False
        for key, val in value.items():
            if key not in columns:
                continue
            new_val, leaf_changed = recurse_redact(val, _leaf)
            new_row[key] = new_val
            changed = changed or leaf_changed
        return new_row, changed

    return recurse_redact(value, _leaf)
