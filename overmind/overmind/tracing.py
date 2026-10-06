"""Overmind SDK tracing: init(), span decorators/context managers, and
Sentry-style helpers. Attribute keys live in :mod:`overmind.attrs`.

Every helper degrades gracefully: without ``init()`` (or without an API key)
decorators call straight through, spans are non-recording, and nothing raises.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import partial, wraps
from pathlib import Path
from typing import Any, TypeVar

from opentelemetry import baggage, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context, attach, detach, get_current, get_value, set_value
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult
from opentelemetry.semconv_ai import SpanAttributes
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from overmind import __version__ as sdk_version
from overmind import analytics, attrs, payloads
from overmind.api import resolve_api_key, resolve_api_url
from overmind.config import DEFAULT_PATH, Config, load
from overmind.evals import MAX_EXPECTATIONS, Expectation
from overmind.genai_usage import canonical_usage_updates
from overmind.slug import identity_slug, split_capability_reference

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

_strict_mode = os.environ.get("OVERMIND_STRICT_MODE", "false").lower() == "true"

_initialized = False
_tracer: trace.Tracer | None = None
_providers: set[str] = set()
_init_lock = threading.Lock()
# Libraries call init() at their entry point, so a keyless user would see the
# "tracing disabled" line on every run — log it once per process, then DEBUG.
_keyless_logged = False


def _local_config() -> Config | None:
    try:
        return load(DEFAULT_PATH) if DEFAULT_PATH.exists() else None
    except (OSError, ValueError):
        return None


def get_api_settings(
    overmind_api_key: str | None = None,
    base_url: str | None = None,
) -> tuple[str, str]:
    """``(api_key, base_url)`` by the same precedence as ``init()`` and the CLI."""
    config = _local_config()
    api_key = resolve_api_key(overmind_api_key or "", config)
    if not api_key:
        raise RuntimeError("Missing Overmind API key. Run `overmind sync` or set OVERMIND_API_KEY.")
    return api_key, resolve_api_url(base_url or "", config)


# provider name -> (importable module gate, instrumentation module, class)
_PROVIDER_MODULES: dict[str, tuple[str, str, str]] = {
    "agno": ("agno", "opentelemetry.instrumentation.agno", "AgnoInstrumentor"),
    "openai": ("openai", "opentelemetry.instrumentation.openai", "OpenAIInstrumentor"),
    "anthropic": ("anthropic", "opentelemetry.instrumentation.anthropic", "AnthropicInstrumentor"),
    "google": ("google.genai", "opentelemetry.instrumentation.google_generativeai", "GoogleGenerativeAiInstrumentor"),
    # Covers LangChain AND LangGraph (the OpenInference instrumentor hooks the
    # shared callback system). Instrumentor ships on ``overmind[tracing]``.
    "langchain": ("langchain_core", "openinference.instrumentation.langchain", "LangChainInstrumentor"),
}


def _module_installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:  # parent namespace package absent
        return False


def _enable_provider(name: str, module: str, instrumentation_module: str, class_name: str) -> None:
    """Instrument *module* if installed. Idempotent; a missing target library
    or missing extra-shipped instrumentor raises only in strict mode.
    Instrumentors import lazily to avoid upfront cost."""
    if name in _providers:
        logger.debug(f"{name} already enabled")
        return

    if not _module_installed(module):
        install_name = module.replace(".", "-")
        msg = f"{install_name} is not installed. Please install it with `pip install {install_name}`."
        if _strict_mode:
            raise ImportError(msg)
        logger.warning(msg)
        return

    if not _module_installed(instrumentation_module):
        msg = f"The {name} instrumentor is not installed. Please install it with `pip install 'overmind[tracing]'`."
        if _strict_mode:
            raise ImportError(msg)
        logger.warning(msg)
        return

    instrumentor_cls = getattr(importlib.import_module(instrumentation_module), class_name)
    instrumentor_cls().instrument()
    _providers.add(name)
    logger.info(f"{name} instrumentation enabled")


def _detect_providers() -> list[str]:
    """Provider names whose target library AND instrumentor are both installed."""
    return [
        name
        for name, (module, instrumentation_module, _) in _PROVIDER_MODULES.items()
        if _module_installed(module) and _module_installed(instrumentation_module)
    ]


def enable_tracing(providers: list[str] | str | None = None) -> None:
    """Instrument the named providers ("openai" / "anthropic" / "google" /
    "agno" / "langchain"); an empty list means all, ``"auto"`` detects and
    enables every provider whose target library and instrumentor are both
    installed. For fan-out setups that skip ``init()`` and export through
    their own TracerProvider."""
    global _initialized, _tracer
    if providers is None:
        return
    if isinstance(providers, str):
        if providers != "auto":
            raise ValueError(f'providers must be a list of provider names or "auto", got {providers!r}')
        providers = _detect_providers()
        logger.info('providers="auto" resolved to: %s', ", ".join(providers) or "none")
    elif providers == []:  # empty list means "all"
        providers = list(_PROVIDER_MODULES)
    if not _initialized:
        provider = trace.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            # Fan-out path (skill Step 3b): the app owns the provider and
            # skips init(). Arm the SDK on it — decorators, start_span, task
            # and deliver gate on _initialized — and attach the stamping +
            # genai processors, or every span lands unscorable.
            _ensure_pipeline_processors(provider)
            _tracer = trace.get_tracer("overmind", sdk_version)
            _initialized = True
    logger.info(f"Enabling tracing for providers: {providers}")
    for name in providers:
        spec = _PROVIDER_MODULES.get(name)
        if spec is None:
            logger.warning(f"Unknown tracing provider: {name!r}")
            continue
        _enable_provider(name, *spec)


# OTel context keys (canonical attribute strings double as keys).
_CTX_KEY_WORKFLOW_NAME = attrs.WORKFLOW_NAME
_CTX_KEY_CONVERSATION_ID = attrs.CONVERSATION_ID
# Context-only key (never a span attribute): a one-shot _PendingTurn cell set
# on entry into a handoff capability scope.
_CTX_KEY_PENDING_TURN = "overmind.capability.pending_turn"


class _PendingTurn:
    """One-shot cell: the first span started inside a handoff capability scope
    consumes it and becomes the new scoring unit's boundary (``unit_kind="turn"``).
    Shared by reference across context copies, so exactly one span wins."""

    __slots__ = ("consumed",)

    def __init__(self) -> None:
        self.consumed = False


# Every "what does this span get stamped with" decision lives here. Creation
# sites (observe / start_span / the turn registry) declare a span's shape as
# creation-time attributes via _declared_attributes(); the on-start processor
# then resolves the final stamps against the ambient context. A task scope
# entered inside an existing span labels it only through _task_scope_may_label.


def _boundary_kind(attributes: Mapping[str, Any]) -> str | None:
    """``"run"`` / ``"turn"`` / None from a span's own attributes; an entry
    point carrying no unit kind counts as a run boundary."""
    kind = attributes.get(attrs.UNIT_KIND)
    if kind in _UNIT_KINDS:
        return str(kind)
    if attributes.get(attrs.SPAN_TYPE) == SpanType.ENTRY_POINT.value:
        return "run"
    return None


def _has_local_parent(span) -> bool:
    """True when the span nests under an in-process span. A remote parent
    (TRACEPARENT into a subprocess) does not count: that process's entry
    point is still its own run boundary."""
    parent = getattr(span, "parent", None)
    return parent is not None and parent.is_valid and not parent.is_remote


def _declared_attributes(
    span_type: SpanType,
    provenance: str | None,
    unit: str | None,
    behaviour_key: str | None = None,
) -> dict[str, str]:
    """Creation-time declaration of a span's role: span type, provenance
    (explicit, else the type's natural class), unit kind (explicit, else
    ``"run"`` on entry points), and — for turn-registry spans — the behaviour
    key the span owns. Passed as start attributes so the on-start resolver
    sees the whole declaration."""
    declared: dict[str, str] = {attrs.SPAN_TYPE: span_type.value}
    if effective := provenance or _SPAN_TYPE_PROVENANCE.get(span_type):
        declared[attrs.PROVENANCE] = effective
    if kind := unit or ("run" if span_type is SpanType.ENTRY_POINT else None):
        declared[attrs.UNIT_KIND] = kind
    if behaviour_key:
        declared[attrs.BEHAVIOUR_KEY] = behaviour_key
    return declared


def _span_processor_on_start(span: trace.Span, parent_context: trace.Context | None = None):
    """Resolve every newborn span's final stamps — the one place unit and key
    decisions are made. States, from the span's declared attributes plus the
    ambient context:

    - every span: identity / workflow / conversation context values;
    - handoff boundary (first span in a pending-turn capability scope):
      ``unit_kind="turn"``, overriding even a declared run — a sub-run
      entered through a handoff is that handoff's unit;
    - declared run with a local parent: demoted to ``"turn"`` — one run
      boundary per trace, the root;
    - declared run at the root: stays ``"run"``, never carries a behaviour key;
    - turn unit / interior span: ambient behaviour key, unless the span
      declared its own key (a turn-registry span is never re-keyed);
    - every recording span: the trace's next ``overmind.step``.

    Values are read from *parent_context* — the context the span was started
    with — so a span started with an explicit ``context=`` is stamped from
    it, not from whatever happens to be attached on this thread.
    """
    if value := get_value(_CTX_KEY_WORKFLOW_NAME, parent_context):
        span.set_attribute(SpanAttributes.TRACELOOP_WORKFLOW_NAME, str(value))
    if capability_name := get_value(attrs.CAPABILITY_NAME, parent_context):
        span.set_attribute(attrs.CAPABILITY_NAME, str(capability_name))
    if capability_slug := get_value(attrs.CAPABILITY_SLUG, parent_context):
        span.set_attribute(attrs.CAPABILITY_SLUG, str(capability_slug))
    if capability_id := get_value(attrs.CAPABILITY_ID, parent_context):
        span.set_attribute(attrs.CAPABILITY_ID, str(capability_id))
    if project_id := get_value(attrs.PROJECT_ID, parent_context):
        span.set_attribute(attrs.PROJECT_ID, str(project_id))
    if conversation_id := get_value(_CTX_KEY_CONVERSATION_ID, parent_context):
        span.set_attribute("conversation.id", str(conversation_id))

    declared = getattr(span, "attributes", None) or {}
    kind = _boundary_kind(declared)
    pending = get_value(_CTX_KEY_PENDING_TURN, parent_context)
    if isinstance(pending, _PendingTurn) and not pending.consumed:
        pending.consumed = True
        kind = "turn"
    elif kind == "run" and _has_local_parent(span):
        kind = "turn"
    if kind is not None and declared.get(attrs.UNIT_KIND) != kind:
        span.set_attribute(attrs.UNIT_KIND, kind)

    behaviour_key = get_value(attrs.BEHAVIOUR_KEY, parent_context)
    if behaviour_key and kind != "run" and attrs.BEHAVIOUR_KEY not in declared:
        span.set_attribute(attrs.BEHAVIOUR_KEY, str(behaviour_key))

    if span.is_recording():
        span.set_attribute(attrs.STEP, _traces.next_step(span.get_span_context().trace_id))


def _task_scope_may_label(span) -> bool:
    """A task scope may label the span it was entered inside only when it can
    prove ownership: the span is recording, is not a unit boundary of any
    kind, and does not already carry a behaviour key set by another owner."""
    if not span.is_recording():
        return False
    attributes = getattr(span, "attributes", None) or {}
    return _boundary_kind(attributes) is None and attrs.BEHAVIOUR_KEY not in attributes


# ``init(export_orphan_spans=True)`` disables suppression; read dynamically so
# a re-init can flip it without rebuilding the provider.
_export_orphan_spans = False
_orphan_suppressed_logged = False


class _OrphanSpanSampler(Sampler):
    """Suppress orphan fragments: a declared ``function`` span that starts a
    NEW local trace (no parent, no boundary declaration) is sampled out, and
    its children fall with it. Everything deliberate still exports — boundary
    declarations (``@entry_point``, ``start_span(unit=...)``, ``run()``),
    other declared span types (a bare ``@tool`` / ``@workflow`` root is a
    choice), foreign spans with no declaration (auto-instrumented roots), and
    anything continuing a remote parent (``TRACEPARENT``)."""

    def should_sample(
        self,
        parent_context,
        trace_id,
        name,
        kind=None,
        attributes=None,
        links=None,
        trace_state=None,
    ) -> SamplingResult:
        parent = trace.get_current_span(parent_context).get_span_context()
        if parent.is_valid:
            keep = parent.is_remote or parent.trace_flags.sampled
            return SamplingResult(Decision.RECORD_AND_SAMPLE if keep else Decision.DROP, attributes, trace_state)
        declared = attributes or {}
        # A delivery span is by definition deliberate — never an orphan fragment.
        is_orphan = (
            _boundary_kind(declared) is None
            and declared.get(attrs.SPAN_TYPE) == SpanType.FUNCTION.value
            and attrs.DELIVERY not in declared
        )
        if _export_orphan_spans or not is_orphan:
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, trace_state)
        global _orphan_suppressed_logged
        if not _orphan_suppressed_logged:
            _orphan_suppressed_logged = True
            logger.warning(
                "span %r started a new trace outside any run boundary and was not exported. "
                "Wrap the call in overmind.run(...) or @overmind.entry_point, or pass "
                "init(export_orphan_spans=True) to export orphan spans.",
                name,
            )
        return SamplingResult(Decision.DROP, attributes, trace_state)

    def get_description(self) -> str:
        return "OvermindOrphanSpanSampler"


class _GenAiUsageSpanProcessor(SpanProcessor):
    """Mirror OTel ``gen_ai.*`` usage onto canonical ``genai.*`` keys at span
    end, so auto-instrumented spans carry the tokens/cost keys the server reads.

    ponytail: mutates ``span._attributes`` (ReadableSpan has no set_attribute).
    Since opentelemetry-sdk 1.43 the ended span's ``BoundedAttributes`` rejects
    item assignment, so we write into its backing ``._dict`` when present and
    fall back to direct assignment for older SDKs. Upgrade path if this seals
    too: a SpanExporter wrapper.
    """

    def on_start(self, span: trace.Span, parent_context: trace.Context | None = None) -> None:
        return

    def on_end(self, span) -> None:
        try:
            self.patch_on_end(span)
        except Exception:
            logger.debug("genai enrichment could not set attributes", exc_info=True)

    def patch_on_end(self, span) -> None:
        updates = canonical_usage_updates(span.attributes or {})
        if not updates:
            return
        target = getattr(span, "_attributes", None)
        if target is None:
            return
        # BoundedAttributes (1.43+) is read-only once the span ends; its plain
        # ``_dict`` backing store still accepts writes and is what ``attributes``
        # proxies. Older SDKs accept assignment on the object itself.
        sink = getattr(target, "_dict", target)
        for key, value in updates.items():
            try:
                sink[key] = value
            except Exception:
                logger.debug("genai enrichment could not set %s", key, exc_info=True)


_GIT_SHA_ENV_VARS = (
    "OVERMIND_GIT_SHA",  # explicit override, checked first
    "GIT_SHA",
    "GIT_COMMIT",
    "GITHUB_SHA",
    "RENDER_GIT_COMMIT",
    "VERCEL_GIT_COMMIT_SHA",
    "HEROKU_SLUG_COMMIT",
    "CI_COMMIT_SHA",
)


def _detect_git_sha(start: Path | None = None) -> str | None:
    """Best-effort commit sha of the running code: env vars first, then
    ``.git/HEAD`` walking up from *start* (default cwd). Never raises,
    never shells out to git."""
    for var in _GIT_SHA_ENV_VARS:
        if sha := os.environ.get(var, "").strip():
            return sha
    try:
        start = start or Path.cwd()
        for directory in (start, *start.parents):
            # ponytail: ``.git`` as a *file* (worktree / submodule) is not
            # resolved; upgrade path is following its ``gitdir:`` pointer.
            head = directory / ".git" / "HEAD"
            if not head.is_file():
                continue
            content = head.read_text(encoding="utf-8").strip()
            if not content.startswith("ref:"):
                return content or None  # detached HEAD holds the sha itself
            ref_name = content[4:].strip()
            ref_file = directory / ".git" / ref_name
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8").strip() or None
            packed = directory / ".git" / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    sha, _, name = line.partition(" ")
                    if name == ref_name:
                        return sha
            return None
    except Exception:
        logger.debug("git sha detection failed", exc_info=True)
    return None


def _seed_identity_context(
    capability_id: str | None,
    capability_name: str | None,
    project_id: str | None,
) -> None:
    """Attach identity values to the OTel context; the on-start processor
    copies them onto every span (including auto-instrumented ones)."""
    if capability_id:
        attach(set_value(attrs.CAPABILITY_ID, str(capability_id)))
    if capability_name:
        attach(set_value(attrs.CAPABILITY_NAME, str(capability_name)))
    if project_id:
        attach(set_value(attrs.PROJECT_ID, str(project_id)))


def _enable_debug_logging() -> None:
    """Make the ``overmind`` logger tree visible even when the host app never
    configured logging: DEBUG level plus one stderr handler."""
    overmind_logger = logging.getLogger("overmind")
    overmind_logger.setLevel(logging.DEBUG)
    if not any(isinstance(handler, logging.StreamHandler) for handler in overmind_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[overmind] %(levelname)s %(name)s: %(message)s"))
        overmind_logger.addHandler(handler)


def _log_debug_summary(
    endpoint: str,
    identity: _Identity,
    flush_interval_ms: int | None = None,
    max_batch_size: int | None = None,
) -> None:
    export = (
        f"batch (flush_interval_ms={flush_interval_ms}, max_batch_size={max_batch_size})"
        if flush_interval_ms is not None
        else "pre-configured provider"
    )
    logger.info(
        "Overmind debug: endpoint=%s | capability_id=%s capability=%s project_id=%s | providers=%s | export=%s | export_orphan_spans=%s",
        endpoint,
        identity.capability_id,
        identity.capability_name,
        identity.project_id,
        ", ".join(sorted(_providers)) or "none",
        export,
        _export_orphan_spans,
    )


def env_identity() -> tuple[str | None, str | None]:
    """The ``(capability_id, capability_name)`` pair from the environment.
    Env identity is all-or-nothing against explicit arguments: a caller that
    declared either half must never pick up the other from a process-global
    env var — a stale env id would silently outrank the declared name
    server-side, and a stale env name would mislabel the id."""
    return os.environ.get("OVERMIND_CAPABILITY_ID"), os.environ.get("OVERMIND_CAPABILITY_NAME")


@dataclass(frozen=True)
class _Identity:
    """Who this process's spans belong to, as ``init()`` resolved it."""

    capability_id: str | None
    capability_name: str | None
    project_id: str | None

    @classmethod
    def resolve(cls, capability_id: str | None, capability: str | None, project_id: str | None) -> _Identity:
        if capability_id is None and capability is None:
            capability_id, capability = env_identity()
        return cls(capability_id, capability, project_id or os.environ.get("OVERMIND_PROJECT_ID"))

    def seed(self) -> None:
        _seed_identity_context(self.capability_id, self.capability_name, self.project_id)

    def resource_attributes(self) -> dict[str, str]:
        # Identity on the resource lets the server resolve Agent/Project directly.
        pairs = {
            attrs.CAPABILITY_ID: self.capability_id,
            attrs.CAPABILITY_NAME: self.capability_name,
            attrs.PROJECT_ID: self.project_id,
        }
        return {key: value for key, value in pairs.items() if value}


def _track_sdk_init(outcome: str, *, providers: list[str] | str | None, identity: _Identity) -> None:
    if providers is None:
        provider_names: list[str] = []
    elif isinstance(providers, str):
        provider_names = [providers]
    else:
        provider_names = list(providers)
    analytics.capture(
        "sdk_init",
        outcome=outcome,
        providers=provider_names,
        has_capability_id=bool(identity.capability_id),
        surface="library",
    )


def init(
    overmind_api_key: str | None = None,
    *,
    service_name: str | None = None,
    environment: str | None = None,
    providers: list[str] | str | None = None,
    overmind_base_url: str | None = None,
    capability_id: str | None = None,
    capability: str | None = None,
    project_id: str | None = None,
    redact_keys: Iterable[str] | None = None,
    export_orphan_spans: bool = False,
    debug: bool = False,
) -> bool:
    """Initialise the Overmind SDK for automatic monitoring.

    Returns True when tracing is active. Without an API key (and outside
    strict mode) it logs, leaves every helper a no-op, and returns False —
    safe to call unconditionally in apps where Overmind is optional.
    Idempotent and thread-safe; re-init refreshes identity, providers, and
    the orphan-export policy only.

    Args:
        overmind_api_key: API key; defaults to the local synced credential, then OVERMIND_API_KEY.
        service_name: Service name in traces; defaults to OVERMIND_SERVICE_NAME.
        environment: e.g. "production"; defaults to OVERMIND_ENVIRONMENT or "development".
        providers: Providers to auto-instrument: "openai", "anthropic", "google",
            "agno", "langchain" (LangChain + LangGraph; needs
            ``overmind[tracing]``). ``"auto"`` detects and enables
            every provider whose library and instrumentor are both installed.
        overmind_base_url: Trace endpoint base URL; defaults to Overmind Cloud.
        capability_id: The capability's UUID from the Console; ingest maps spans
            to tasks and behaviours by this id alone. Defaults to OVERMIND_CAPABILITY_ID.
        capability: Display label stamped beside the id; never resolves a
            capability. Defaults to OVERMIND_CAPABILITY_NAME.
        project_id: Project UUID, only needed for session auth; defaults to OVERMIND_PROJECT_ID.
        redact_keys: Extra dict keys (exact, case-insensitive) redacted from
            captured inputs/outputs, on top of the built-in secret patterns.
        export_orphan_spans: Export ``function`` spans that start a new trace
            outside any run boundary. Off by default — the platform quarantines
            such single-fragment traces as noise (see
            ``docs/tracing-attributes.md`` §7).
        debug: Log a one-line setup summary (endpoint, identity, enabled
            instrumentors, export mode) and raise the ``overmind`` logger to
            DEBUG with a stderr handler.
    """
    global _export_orphan_spans

    if debug:
        _enable_debug_logging()
    identity = _Identity.resolve(capability_id, capability, project_id)
    _export_orphan_spans = bool(export_orphan_spans)
    if redact_keys:
        payloads.add_redact_keys(redact_keys)

    with _init_lock:
        if _initialized:
            # Re-init only refreshes identity + providers; exporters stay as-is.
            logger.debug(f"Overmind SDK already initialised, reinitialising with providers: {providers}")
            identity.seed()
            enable_tracing(providers)
            if debug:
                _log_debug_summary("(unchanged — already initialised)", identity)
        elif os.environ.get("OVERMIND_TRACE_FILE") and not (overmind_api_key or os.environ.get("OVERMIND_API_KEY")):
            _adopt_trace_file_provider(identity, providers, debug=debug)
        else:
            config = _local_config()
            api_key = resolve_api_key(overmind_api_key or "", config)
            if not api_key:
                return _stay_keyless(identity, providers)
            if not identity.project_id and config and config.project_id:
                identity = replace(identity, project_id=config.project_id)
            _install_exporting_provider(
                api_key,
                resolve_api_url(overmind_base_url or "", config),
                identity,
                providers,
                service_name=service_name,
                environment=environment,
                debug=debug,
            )
        _track_sdk_init("success", providers=providers, identity=identity)
        return True


def _adopt_trace_file_provider(identity: _Identity, providers: list[str] | str | None, *, debug: bool) -> None:
    """Optimise-step subprocess: the runner wrapper set up a file-exporter
    provider (``OVERMIND_TRACE_FILE``) and stripped the API key. Reuse it
    instead of crashing or replacing the exporter."""
    logger.debug(
        "Overmind SDK init() skipped: OVERMIND_TRACE_FILE is set and no "
        "OVERMIND_API_KEY available; reusing the local file-exporter "
        "TracerProvider configured by the optimise runner wrapper.",
    )
    _ensure_pipeline_processors(trace.get_tracer_provider())
    _arm(identity, providers)
    if debug:
        _log_debug_summary(f"file:{os.environ['OVERMIND_TRACE_FILE']}", identity)


def _stay_keyless(identity: _Identity, providers: list[str] | str | None) -> bool:
    global _keyless_logged
    message = "Overmind tracing disabled: no project credential. Run `overmind sync` or set OVERMIND_API_KEY."
    if _strict_mode:
        _track_sdk_init("strict-fail", providers=providers, identity=identity)
        raise RuntimeError(message)
    logger.log(logging.DEBUG if _keyless_logged else logging.INFO, message)
    _keyless_logged = True
    _track_sdk_init("keyless", providers=providers, identity=identity)
    return False


def _install_exporting_provider(
    api_key: str,
    base_url: str,
    identity: _Identity,
    providers: list[str] | str | None,
    *,
    service_name: str | None,
    environment: str | None,
    debug: bool,
) -> None:
    environment = (
        environment or os.environ.get("OVERMIND_ENVIRONMENT") or os.environ.get("ENVIRONMENT") or "development"
    )
    resource_attributes = {
        "service.name": service_name or os.environ.get("OVERMIND_SERVICE_NAME") or "overmind-telemetry",
        "service.version": os.environ.get("SERVICE_VERSION", sdk_version),
        "deployment.environment": environment,
        attrs.SDK_NAME: "overmind-python",
        attrs.SDK_VERSION: sdk_version,
        **identity.resource_attributes(),
    }
    # Commit sha binds every trace to the exact code the process runs.
    if git_sha := _detect_git_sha():
        resource_attributes[attrs.VCS_REF_HEAD_REVISION] = git_sha

    provider = TracerProvider(resource=Resource.create(resource_attributes), sampler=_OrphanSpanSampler())
    # Must run before the exporting processor so its on-end mutation is exported.
    provider.add_span_processor(_GenAiUsageSpanProcessor())
    provider.add_span_processor(_TurnLifecycleSpanProcessor())

    endpoint = f"{base_url}/api/v1/traces"
    # Flush every ~2s (OTel default 5s) so progress streams while spans are open.
    flush_interval_ms = int(os.environ.get("OVERMIND_SPAN_FLUSH_INTERVAL_MS", "2000"))
    max_batch_size = int(os.environ.get("OVERMIND_SPAN_MAX_EXPORT_BATCH_SIZE", "256"))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, headers={"X-Api-Key": api_key}),
            schedule_delay_millis=flush_interval_ms,
            max_export_batch_size=max_batch_size,
        )
    )
    provider._overmind_processors_attached = True  # noqa: SLF001
    trace.set_tracer_provider(provider)

    _arm(identity, providers)
    logger.info(f"Overmind SDK initialised: service={service_name}, environment={environment}")
    if debug:
        _log_debug_summary(endpoint, identity, flush_interval_ms=flush_interval_ms, max_batch_size=max_batch_size)


def _arm(identity: _Identity, providers: list[str] | str | None) -> None:
    """Point the SDK at the installed provider; decorators record from here on."""
    global _initialized, _tracer
    _tracer = trace.get_tracer("overmind", sdk_version)
    identity.seed()
    enable_tracing(providers)
    _attach_environment_carrier()
    _initialized = True


def get_tracer() -> trace.Tracer:
    """Return the Overmind tracer; raises RuntimeError if not initialised."""
    if not _initialized or _tracer is None:
        raise RuntimeError("Overmind SDK not initialised. Call overmind.init() first.")
    return _tracer


def set_user(user_id: str, email: str | None = None, username: str | None = None) -> None:
    """Associate the current trace with a user (like Sentry's ``set_user``)."""
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("user.id", user_id)
        if email:
            span.set_attribute("user.email", email)
        if username:
            span.set_attribute("user.username", username)


def _safe_set_attribute(otel_span, key: str, value: Any) -> None:
    otel_span.set_attribute(key, payloads.to_attribute(value))


def set_tag(key: str, value) -> None:
    """Add a custom tag to the current span; rich values are JSON-encoded."""
    span = trace.get_current_span()
    if not span.is_recording():
        logger.debug("set_tag(%s=…) ignored: current span has ended %s", key, span)
        return
    _safe_set_attribute(span, key, value)


def capture_exception(exception: Exception) -> None:
    """Record an exception on the current span and mark it as errored."""
    span = trace.get_current_span()
    if span.is_recording():
        span.record_exception(exception)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(exception)))


def set_workflow_name(workflow_name: str) -> None:
    """Attach a Traceloop-compatible workflow label to every subsequent span."""
    attach(set_value(attrs.WORKFLOW_NAME, workflow_name))


def set_conversation_id(conversation_id: str) -> None:
    """Tag downstream spans with a stable ``conversation.id`` for session grouping."""
    attach(set_value(attrs.CONVERSATION_ID, conversation_id))


def _is_handoff(name: str | None, id: str | None) -> bool:
    """Entering a capability that differs from the active one, mid-trace.

    Identity is compared on the finest shared grain: ids when both sides
    have one (a name never shadows an id), else names on the slug grain —
    the server resolves a slug and its display spelling to the same
    capability, so they are not different identities. Mixed grains (id-only
    scope under name-only identity) are never treated as a handoff — a
    boundary is only declared when the identities are provably different."""
    if not trace.get_current_span().get_span_context().is_valid:
        return False
    active_id = get_value(attrs.CAPABILITY_ID)
    active_name = get_value(attrs.CAPABILITY_NAME)
    if id and active_id:
        return str(id) != str(active_id)
    if name and active_name:
        return identity_slug(str(name)) != identity_slug(str(active_name))
    return False


def _capability_context(name: str | None, id: str | None, *, slug: str | None = None) -> Context:
    """The current context with *name* / *id* / *slug* as the capability identity.
    Entering a different identity mid-trace arms a one-shot handoff turn."""
    ctx = set_value(_CTX_KEY_PENDING_TURN, _PendingTurn() if _is_handoff(name or slug, id) else None)
    # Keys are always written: a name-only scope must not inherit the outer
    # scope's id (the server resolves id before slug before name).
    ctx = set_value(attrs.CAPABILITY_NAME, name, ctx)
    ctx = set_value(attrs.CAPABILITY_SLUG, slug, ctx)
    ctx = set_value(attrs.CAPABILITY_ID, id, ctx)
    # Behaviour keys are capability-scoped: a key declared under the outer
    # capability means nothing here, so the ambient key resets with the
    # identity (detach restores it once this scope closes).
    return set_value(attrs.BEHAVIOUR_KEY, None, ctx)


class _CapabilityScope:
    """Context manager (sync or async) and decorator produced by
    :func:`capability`. Entering attaches the capability identity to the OTel
    context (async-safe via contextvars) so the on-start processor stamps it
    on every span created inside; exiting restores the outer identity.

    Decorating a function that is not already ``observe``-wrapped makes it an
    entry-point span; stacking over ``@tool`` / ``@observe`` only opens the
    identity scope so the inner decorator owns the span."""

    def __init__(
        self,
        name: str | None,
        id: str | None,
        *,
        slug: str | None = None,
        description: str = "",
    ) -> None:
        if not name and not id and not slug:
            raise ValueError("capability() requires a slug, name and/or id")
        self._name = str(name) if name else None
        self._id = str(id) if id else None
        self._slug = str(slug) if slug else None
        self._description = str(description or "")
        self._token: Any = None

    def __enter__(self) -> _CapabilityScope:
        self._token = attach(_capability_context(self._name, self._id, slug=self._slug))
        return self

    def __exit__(self, *exc) -> None:
        if self._token is not None:
            detach(self._token)
            self._token = None

    async def __aenter__(self) -> _CapabilityScope:
        return self.__enter__()

    async def __aexit__(self, *exc) -> None:
        self.__exit__(*exc)

    def __call__(self, func: F) -> F:
        # Already observed (e.g. ``@capability`` over ``@tool``): scope only.
        if getattr(func, "_overmind_observed", False):
            if inspect.iscoroutinefunction(func):

                @wraps(func)
                async def async_scoped(*args, **kwargs):
                    with self:
                        return await func(*args, **kwargs)

                return async_scoped  # type: ignore[return-value]

            @wraps(func)
            def sync_scoped(*args, **kwargs):
                with self:
                    return func(*args, **kwargs)

            return sync_scoped  # type: ignore[return-value]

        # Bare function: entry point + identity.
        return observe(
            type=SpanType.ENTRY_POINT,
            capability=self._slug or self._name,
            capability_id=self._id,
        )(func)


def capability(
    slug: str | None = None,
    *,
    description: str = "",
    id: str | None = None,
    name: str | None = None,
) -> _CapabilityScope:
    """Declare that all work inside belongs to one capability.

    The positional argument is the capability's slug when it is already
    lowercase kebab-case; otherwise it is treated as a display name and a
    slug is derived. ``description`` is recorded for the AST manifest scan
    and does not affect runtime spans.

    ``id`` — the capability's UUID from the Console — is resolved first when
    present and is stable through renames.

    Usable as a context manager (``with`` / ``async with``) or decorator.
    Decorating a bare function makes it an entry-point span; stacking over
    ``@tool`` / ``@observe`` only opens the identity scope. Every span created
    inside carries ``overmind.capability.slug`` / ``.name`` / ``.id``; on exit
    the outer identity is restored. Entering a *different* capability mid-trace
    is a handoff: the first span of the new scope is stamped
    ``overmind.unit_kind = "turn"``. The identity must be one the project
    declared — nothing is auto-created."""
    if not slug and not id and not name:
        raise ValueError("capability() requires a slug, name and/or id")
    resolved_slug: str | None = None
    display: str | None = name
    if slug:
        reference_display, resolved_slug = split_capability_reference(slug)
        display = display or reference_display
    elif name:
        resolved_slug = identity_slug(str(name)) or None
    return _CapabilityScope(display, id, slug=resolved_slug, description=description)


class _TurnRegistry:
    """Open turn-unit spans keyed by (trace_id, behaviour key).

    A behaviour's activity is non-contiguous in loop-shaped agents (debate
    rounds interleave with other phases), so its turn span outlives each task
    scope; it ends when the trace's run-boundary span ends (or at flush as a
    backstop), with the last scope-exit time so durations stay truthful."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: dict[tuple[int, str], trace.Span] = {}
        self._last_activity_ns: dict[tuple[int, str], int] = {}

    def get_or_start(self, key: str) -> trace.Span:
        ambient = trace.get_current_span().get_span_context()
        evicted: list[tuple[trace.Span, int | None]] = []
        with self._lock:
            if ambient.is_valid and (span := self._spans.pop((ambient.trace_id, key), None)) is not None:
                # Re-insert so eviction takes the least recently used entry.
                self._spans[(ambient.trace_id, key)] = span
                return span
            span = get_tracer().start_span(
                key, attributes=_declared_attributes(SpanType.FUNCTION, None, "turn", behaviour_key=key)
            )
            self._spans[(span.get_span_context().trace_id, key)] = span
            # Without a run boundary nothing ever ends these; cap like the
            # evidence registry so a long-lived process cannot leak live spans.
            while len(self._spans) > _MAX_TRACKED_TRACES:
                entry = next(iter(self._spans))
                evicted.append((self._spans.pop(entry), self._last_activity_ns.pop(entry, None)))
        for old_span, end_ns in evicted:
            old_span.end(end_time=end_ns)
        return span

    def touch(self, span: trace.Span, key: str) -> None:
        entry = (span.get_span_context().trace_id, key)
        with self._lock:
            # Only a still-tracked span records activity — a touch after
            # eviction must not resurrect the bookkeeping for an ended span.
            if (live := self._spans.pop(entry, None)) is not None:
                self._spans[entry] = live
                self._last_activity_ns[entry] = time.time_ns()

    def end_for_trace(self, trace_id: int) -> None:
        self._end(lambda entry: entry[0] == trace_id)

    def end_all(self) -> None:
        self._end(lambda entry: True)

    def _end(self, match: Callable[[tuple[int, str]], bool]) -> None:
        with self._lock:
            entries = [entry for entry in self._spans if match(entry)]
            ended = [(self._spans.pop(entry), self._last_activity_ns.pop(entry, None)) for entry in entries]
        # end() re-enters the processor chain (export, _span_processor_on_end),
        # so it must run outside the lock.
        for span, end_ns in ended:
            span.end(end_time=end_ns)


_turn_registry = _TurnRegistry()


def _span_processor_on_end(span) -> None:
    """End the trace's open turn spans when its run-boundary span ends, and
    forget the trace's registry entry so it stays bounded."""
    if _boundary_kind(getattr(span, "attributes", None) or {}) != "run":
        return
    ctx = span.get_span_context()
    if ctx.is_valid:
        _turn_registry.end_for_trace(ctx.trace_id)
        _traces.drop(ctx.trace_id)


class _TurnLifecycleSpanProcessor(SpanProcessor):
    """First-class home for the stamping resolver and the turn lifecycle, so
    every provider that carries it gets identity/unit stamping — including
    providers ``init()`` did not build."""

    def on_start(self, span: trace.Span, parent_context: trace.Context | None = None) -> None:
        _span_processor_on_start(span, parent_context)

    def on_end(self, span) -> None:
        _span_processor_on_end(span)


def _ensure_pipeline_processors(provider) -> None:
    """Attach the genai-enrichment and stamping/turn processors once to an SDK
    provider the SDK did not construct (trace-file runner, fan-out setups)."""
    if not isinstance(provider, TracerProvider):
        return
    if getattr(provider, "_overmind_processors_attached", False):
        return
    provider.add_span_processor(_GenAiUsageSpanProcessor())
    provider.add_span_processor(_TurnLifecycleSpanProcessor())
    provider._overmind_processors_attached = True  # noqa: SLF001


class _TaskScope:
    """Context manager and decorator produced by :func:`task`. Stamps
    ``overmind.behaviour.key`` on every span created inside, and on the span
    it was entered inside when :func:`_task_scope_may_label` proves ownership
    (never a unit boundary, never over an existing key); exiting restores the
    outer key. With ``unit="turn"`` the scope instead makes the behaviour's
    turn span (lazily created, re-used across re-entries) the current span."""

    def __init__(self, key: str, unit: str | None = None) -> None:
        key = (key or "").strip()
        if not key:
            raise ValueError("task() requires a key")
        if unit is not None and unit != "turn":
            raise ValueError(
                f'task() unit must be "turn", got {unit!r} — run boundaries are '
                'declared by entry points / start_span(unit="run")'
            )
        self._key = key
        self._unit = unit
        self._token: Any = None
        self._turn_span: trace.Span | None = None

    def __enter__(self) -> _TaskScope:
        ctx = set_value(attrs.BEHAVIOUR_KEY, self._key)
        if self._unit == "turn" and _initialized:
            self._turn_span = _turn_registry.get_or_start(self._key)
            ctx = trace.set_span_in_context(self._turn_span, ctx)
        self._token = attach(ctx)
        if self._turn_span is None:
            span = trace.get_current_span()
            if _task_scope_may_label(span):
                span.set_attribute(attrs.BEHAVIOUR_KEY, self._key)
        return self

    def __exit__(self, *exc) -> None:
        if self._turn_span is not None:
            _turn_registry.touch(self._turn_span, self._key)
            self._turn_span = None
        if self._token is not None:
            detach(self._token)
            self._token = None

    async def __aenter__(self) -> _TaskScope:
        return self.__enter__()

    async def __aexit__(self, *exc) -> None:
        self.__exit__(*exc)

    def __call__(self, func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                with _TaskScope(self._key, self._unit):
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            with _TaskScope(self._key, self._unit):
                return func(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]


def task(key: str, *, unit: str | None = None) -> _TaskScope:
    """Declare the Behaviour.slug this work belongs to.

    Usable as a context manager (``with`` / ``async with``) or decorator.
    Optional — the server binds structurally when this is absent. No-op when
    nothing is recording (SDK not initialised). Restores the outer key on exit.

    ``unit="turn"`` additionally makes the scope a scoring unit: it lazily
    opens one turn span per (trace, key) that spans created inside nest under.
    Re-entering the same key re-uses the still-open span, so a phase's
    non-contiguous activity lands in one unit; the span ends when the trace's
    run-boundary span ends, at the phase's last scope-exit time."""
    return _TaskScope(key, unit)


class SpanType(str, Enum):
    FUNCTION = "function"
    ENTRY_POINT = "entry_point"
    WORKFLOW = "workflow"
    TOOL = "tool_call"
    LLM = "llm_call"
    RETRIEVAL = "retrieval"


def _coerce_span_type(value: SpanType | str) -> SpanType:
    """Accept SpanType members, wire values ("tool_call"), or friendly names
    ("tool", "llm", "entry_point")."""
    if isinstance(value, SpanType):
        return value
    try:
        return SpanType(value)
    except ValueError:
        member = getattr(SpanType, str(value).upper(), None)
        if member is None:
            raise ValueError(f"unknown span type {value!r}") from None
        return member


_PROVENANCE_VALUES = frozenset({"user", "agent", "environment", "harness"})
_UNIT_KINDS = frozenset({"turn", "run"})
_CAPTURE_MODES = frozenset({"auto", "none", "messages"})

# Span types whose payloads have an unambiguous provenance class: tool results
# and retrieved documents are environment observations, model completions are
# agent-authored.  Everything else needs an explicit ``provenance=``.
_SPAN_TYPE_PROVENANCE = {
    SpanType.TOOL: "environment",
    SpanType.RETRIEVAL: "environment",
    SpanType.LLM: "agent",
}


def _validate_provenance(value: str | None) -> None:
    if value is not None and value not in _PROVENANCE_VALUES:
        raise ValueError(f"provenance must be one of {sorted(_PROVENANCE_VALUES)}, got {value!r}")


def _validate_unit(value: str | None) -> None:
    if value is not None and value not in _UNIT_KINDS:
        raise ValueError(f"unit must be one of {sorted(_UNIT_KINDS)}, got {value!r}")


# Per-trace registry — keyed by trace id, not a ContextVar: threads, isolated
# event loops and remote hops share a trace id but not necessarily a context.

_MAX_TRACKED_TRACES = 256


@dataclass
class _TraceState:
    step: int = 0
    evidence: list[str] = field(default_factory=list)  # environment-provenance span ids
    tool_calls: dict[str, trace.SpanContext] = field(default_factory=dict)  # call id -> requesting LLM span


class _TraceRegistry:
    """Lock-guarded LRU of :class:`_TraceState`, capped so a process that
    never closes a run boundary cannot grow it forever. An entry is dropped
    when its run-boundary span ends."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[int, _TraceState] = {}

    def _state(self, trace_id: int) -> _TraceState:
        # Re-insert at the end so eviction takes the least recently touched trace.
        state = self._states.pop(trace_id, None) or _TraceState()
        self._states[trace_id] = state
        while len(self._states) > _MAX_TRACKED_TRACES:
            self._states.pop(next(iter(self._states)))
        return state

    def next_step(self, trace_id: int) -> int:
        with self._lock:
            state = self._state(trace_id)
            state.step += 1
            return state.step

    def current_step(self, trace_id: int) -> int:
        with self._lock:
            state = self._states.get(trace_id)
            return state.step if state else 0

    def witness_step(self, trace_id: int, step: int) -> None:
        """Lamport receive: this side's next step sorts after *step*."""
        with self._lock:
            state = self._state(trace_id)
            state.step = max(state.step, step)

    def remember_evidence(self, span_context: trace.SpanContext) -> None:
        with self._lock:
            self._state(span_context.trace_id).evidence.append(format(span_context.span_id, "016x"))

    def take_evidence(self, trace_id: int) -> list[str]:
        with self._lock:
            state = self._states.get(trace_id)
            if state is None:
                return []
            evidence, state.evidence = state.evidence, []
            return evidence

    def remember_tool_call(self, span_context: trace.SpanContext, call_id: str) -> None:
        """Record the LLM span that requested *call_id*; the first writer wins."""
        with self._lock:
            self._state(span_context.trace_id).tool_calls.setdefault(call_id, span_context)

    def tool_call_origin(self, trace_id: int, call_id: str) -> trace.SpanContext | None:
        with self._lock:
            state = self._states.get(trace_id)
            return state.tool_calls.get(call_id) if state else None

    def drop(self, trace_id: int) -> None:
        with self._lock:
            self._states.pop(trace_id, None)


_traces = _TraceRegistry()


class _Outcome(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"  # KeyboardInterrupt / asyncio.CancelledError
    ABORTED = "aborted"  # a generator closed by its consumer before exhaustion

    @classmethod
    def of(cls, exc: BaseException) -> _Outcome:
        return cls.CANCELLED if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else cls.FAILED


def _end_span(otel_span: trace.Span, outcome: _Outcome, exc: BaseException | None = None) -> None:
    """The one writer of lifecycle status; ends the span. An aborted span
    keeps the OTel status UNSET: the consumer stopped, the function did not fail."""
    otel_span.set_attribute(attrs.STATUS, outcome.value)
    if outcome is _Outcome.SUCCESS:
        otel_span.set_status(Status(StatusCode.OK))
    elif outcome is _Outcome.CANCELLED:
        otel_span.record_exception(exc)
        otel_span.set_status(Status(StatusCode.ERROR, f"Cancelled ({type(exc).__name__})"))
    elif outcome is _Outcome.FAILED:
        otel_span.set_attribute(attrs.ERROR_TYPE, type(exc).__name__)
        otel_span.set_attribute(attrs.ERROR_MESSAGE, str(exc)[:1024])
        otel_span.record_exception(exc)
        otel_span.set_status(Status(StatusCode.ERROR, str(exc)))
    otel_span.end()


_TRUNCATED_MARKER = {"inputs": attrs.INPUTS_TRUNCATED, "outputs": attrs.OUTPUTS_TRUNCATED}


def _set_payload(otel_span: trace.Span, key: str, value: Any) -> None:
    """The only writer of ``inputs`` / ``outputs``: bounded JSON plus the
    truncation marker when the budget was exceeded."""
    text, truncated = payloads.serialize_payload(value)
    otel_span.set_attribute(key, text)
    if truncated:
        otel_span.set_attribute(_TRUNCATED_MARKER[key], True)


# Set by tool_call(); the next tool span started in this context consumes it.
_pending_tool_call: ContextVar[str | None] = ContextVar("overmind.pending_tool_call", default=None)


@contextmanager
def tool_call(call_id: str) -> Iterator[None]:
    """Bind the next tool span started inside this block to the model-emitted
    *call_id*: the span carries ``gen_ai.tool.call.id`` and links to the LLM
    span whose output requested it (the SDK remembers the ids an observed
    LLM span emits in its messages or its stream). One-shot and
    context-local — enter it in the task or thread that runs the tool."""
    if not call_id:
        raise ValueError("tool_call() requires a call id")
    token = _pending_tool_call.set(str(call_id))
    try:
        yield
    finally:
        _pending_tool_call.reset(token)


def _start_span(name: str, declared: Mapping[str, Any], context: Context) -> trace.Span:
    """The one start site for SDK-created spans. A tool span started inside
    :func:`tool_call` takes the pending id here: links must be known at start."""
    links: tuple[trace.Link, ...] = ()
    call_id = _pending_tool_call.get() if declared.get(attrs.SPAN_TYPE) == SpanType.TOOL.value else None
    if call_id:
        _pending_tool_call.set(None)
        parent = trace.get_current_span(context).get_span_context()
        origin = _traces.tool_call_origin(parent.trace_id, call_id) if parent.is_valid else None
        if origin is not None:
            links = (trace.Link(origin, {attrs.TOOL_CALL_ID: call_id}),)
    span = get_tracer().start_span(name, context=context, attributes=declared, links=links)
    if call_id and span.is_recording():
        span.set_attribute(attrs.TOOL_CALL_ID, call_id)
    return span


def _target(func: Callable) -> Callable:
    """The code behind *func*: through ``__wrapped__``, ``functools.partial``
    and a callable instance's ``__call__``."""
    func = inspect.unwrap(func)
    if isinstance(func, partial):
        return _target(func.func)
    if not inspect.isroutine(func) and not isinstance(func, type) and callable(func):
        return _target(type(func).__call__)
    return func


def code_identity_attributes(func: Callable) -> dict[str, str | int]:
    """``code.*`` attributes of the *unwrapped* function so the server can
    bind the span to a code-symbol anchor (``module.qualname``). Best-effort,
    never raises."""
    out: dict[str, str | int] = {}
    try:
        target = _target(func)
        if module := getattr(target, "__module__", None):
            out[attrs.CODE_NAMESPACE] = module
        if qualname := getattr(target, "__qualname__", None):
            out[attrs.CODE_FUNCTION_NAME] = qualname
        if code := getattr(target, "__code__", None):
            out[attrs.CODE_FILE_PATH] = code.co_filename
            out[attrs.CODE_LINE_NUMBER] = code.co_firstlineno
    except Exception:
        logger.debug("observe(): code identity capture failed", exc_info=True)
    return out


def _signature(func: Callable) -> inspect.Signature | None:
    try:
        return inspect.signature(func)
    except (TypeError, ValueError):
        return None


def _bind_arguments(
    signature: inspect.Signature | None, args: tuple, kwargs: dict, ignore: frozenset[str]
) -> dict[str, Any]:
    """Name the call's arguments; drop ``self``/``cls``, ignored names and
    never-serialised runtime values. Without a usable signature the
    positionals stay together under ``args``. Never raises."""
    bound: dict[str, Any] = {"args": list(args), **kwargs}
    if signature is not None:
        try:
            bound = dict(signature.bind_partial(*args, **kwargs).arguments)
        except TypeError:
            pass
        else:
            for name, parameter in signature.parameters.items():
                if parameter.kind is inspect.Parameter.VAR_KEYWORD and name in bound:
                    bound.update(bound.pop(name))
            first = next(iter(signature.parameters), None)
            if first in ("self", "cls"):
                bound.pop(first, None)
    return {k: v for k, v in bound.items() if k not in ignore and not payloads.should_skip(v)}


@dataclass(frozen=True)
class _ObserveSpec:
    """Everything :func:`observe` decides once per decorated function."""

    name: str | Callable[..., str] | None
    default_name: str  # the unwrapped function's qualname
    short_name: str  # its __name__; tool.name when the name is not overridden
    span_type: SpanType
    declared: Mapping[str, Any]
    capability: str | None
    capability_id: str | None
    capture: str
    ignore: frozenset[str]
    input_key: str | None
    format_input: Callable[[dict[str, Any]], Any] | None
    format_output: Callable[[Any, dict[str, Any]], Any] | None
    expectations: tuple[Expectation, ...]
    signature: inspect.Signature | None
    prompt: str | None

    def names(self, args: tuple, kwargs: dict) -> tuple[str, str]:
        """``(span name, tool.name)``; a per-call name callable names both."""
        if isinstance(self.name, str):
            return self.name, self.name
        if callable(self.name):
            try:
                resolved = str(self.name(*args, **kwargs))
            except Exception:
                logger.debug("observe(): span name callable failed for %s", self.default_name, exc_info=True)
            else:
                return resolved, resolved
        return self.default_name, self.short_name


_warned_expectations: set[str] = set()


def _warn_expectations_dropped(qualname: str) -> None:
    if qualname in _warned_expectations:
        return
    _warned_expectations.add(qualname)
    logger.warning("expectations declared on %s were not emitted: the SDK is not initialised", qualname)


class _ObservedCall:
    """One invocation of an observed callable: its span, the context that
    makes the span current, its bound arguments and, for generators, the
    stream fold.

    The span is current only inside :meth:`current`. Plain and async
    functions take one step; generators take one per resume, so consumer
    work between items never nests under the generator span. Without
    ``init()`` the span is ``INVALID_SPAN`` and nothing is attached."""

    __slots__ = ("spec", "span", "context", "bound", "stream", "items", "ended")

    def __init__(self, spec: _ObserveSpec, args: tuple, kwargs: dict, *, streaming: bool = False) -> None:
        self.spec = spec
        self.bound: dict[str, Any] = {}
        self.stream = payloads.StreamedMessage() if streaming else None
        # Yielded items are kept only for a format_output hook to see.
        self.items: list[Any] | None = [] if streaming and spec.format_output is not None else None
        self.ended = False
        name, tool_name = spec.names(args, kwargs)
        if spec.capability or spec.capability_id:
            display, slug = split_capability_reference(spec.capability) if spec.capability else (None, None)
            ctx = _capability_context(display, spec.capability_id, slug=slug)
        else:
            ctx = get_current()
        if not _initialized:
            if spec.expectations:
                _warn_expectations_dropped(spec.default_name)
            self.span, self.context = trace.INVALID_SPAN, ctx
            return
        self.span = _start_span(name, spec.declared, ctx)
        self.context = trace.set_span_in_context(self.span, ctx)
        if not self.span.is_recording():
            return
        self.bound = _bind_arguments(spec.signature, args, kwargs, spec.ignore)
        if spec.span_type is SpanType.TOOL:
            self.span.set_attribute(attrs.TOOL_NAME, tool_name)
        if spec.prompt:
            self.span.set_attribute(attrs.PROMPT_TEMPLATE, spec.prompt)
        if spec.capture != "none":
            self._capture("inputs", self._input_payload)
        for expectation in spec.expectations:
            expectation.emit(self.span)

    @contextmanager
    def current(self) -> Iterator[None]:
        """Make the span current for one step; detached on the same thread or task."""
        if not self.span.get_span_context().is_valid:
            yield
            return
        token = attach(self.context)
        try:
            yield
        finally:
            detach(token)

    def observe_item(self, item: Any) -> None:
        """Generators only, between steps: fold *item* and remember the tool
        calls it introduced as requested by this span."""
        if not self.span.is_recording():
            return
        if self.items is not None:
            self.items.append(item)
        for call_id in self.stream.add(item):
            _traces.remember_tool_call(self.span.get_span_context(), call_id)

    def succeed(self, result: Any = None) -> None:
        if self.ended:
            return
        self.ended = True
        if self.span.is_recording():
            if self.spec.capture != "none":
                self._capture("outputs", lambda: self._output_payload(result))
            self._finish_recording()
            if self.spec.span_type is SpanType.LLM and self.stream is None:
                for call_id in payloads.requested_tool_call_ids(result):
                    _traces.remember_tool_call(self.span.get_span_context(), call_id)
            if self.spec.declared.get(attrs.PROVENANCE) == "environment":
                _traces.remember_evidence(self.span.get_span_context())
        _end_span(self.span, _Outcome.SUCCESS)

    def fail(self, exc: BaseException) -> None:
        if self.ended:
            return
        self.ended = True
        outcome = _Outcome.of(exc)
        if self.span.is_recording():
            if self.spec.span_type is SpanType.TOOL:
                self.span.set_attribute(attrs.TOOL_ERROR, type(exc).__name__)
            self._finish_recording()
        _end_span(self.span, outcome, exc)
        # Flushing the run root keeps a cancellation from leaving the trace
        # rootless until the batch timeout.
        if self.spec.span_type is SpanType.ENTRY_POINT and outcome is _Outcome.CANCELLED:
            force_flush_traces(timeout_millis=5000)

    def abort(self) -> None:
        """The consumer closed the generator early; keep what was streamed."""
        if self.ended:
            return
        self.ended = True
        if self.span.is_recording():
            if self.spec.capture != "none":
                self._capture("outputs", lambda: self._output_payload(None))
            self._finish_recording()
        _end_span(self.span, _Outcome.ABORTED)

    def _capture(self, key: str, build: Callable[[], Any]) -> None:
        try:
            _set_payload(self.span, key, build())
        except Exception:
            logger.debug("observe(): %s capture failed for %s", key, self.spec.default_name, exc_info=True)

    def _finish_recording(self) -> None:
        if self.stream is not None:
            self.span.set_attribute(attrs.STREAM_ITEMS, self.stream.count)

    def _input_payload(self) -> Any:
        spec = self.spec
        if spec.format_input is not None:
            return spec.format_input(self.bound)
        if spec.input_key is not None:
            return self.bound[spec.input_key]
        if spec.capture == "messages":
            return {
                "messages": payloads.normalize_messages(self.bound.get("messages") or self.bound.get("input_messages"))
            }
        return self.bound

    def _output_payload(self, result: Any) -> Any:
        spec = self.spec
        if spec.format_output is not None:
            return spec.format_output(self.items if self.items is not None else result, self.bound)
        if self.stream is not None:
            if spec.capture == "messages":
                message = self.stream.message()
                return {"messages": [message] if message else []}
            return result if result is not None else {"items": self.stream.count}
        if spec.capture == "messages" and isinstance(result, list):
            return {"messages": payloads.normalize_messages(result)}
        return result


def _wrap(func: Callable, spec: _ObserveSpec) -> Callable:
    """Pick the wrapper by the callable's kind. A ``staticmethod`` /
    ``classmethod`` applied under ``observe`` is traced through its function
    and the descriptor rebuilt, so decorator order never breaks a method."""
    if isinstance(func, (staticmethod, classmethod)):
        result = type(func)(_wrap(func.__func__, spec))
        result._overmind_observed = True  # type: ignore[attr-defined]
        return result
    target = _target(func)

    if inspect.isasyncgenfunction(target):

        async def async_generator_wrapper(*args, **kwargs):
            call = _ObservedCall(spec, args, kwargs, streaming=True)
            gen = func(*args, **kwargs)
            sent, thrown = None, None
            try:
                while True:
                    with call.current():
                        try:
                            item = await (gen.asend(sent) if thrown is None else gen.athrow(thrown))
                        except StopAsyncIteration:
                            call.succeed()
                            return
                    call.observe_item(item)
                    try:
                        sent, thrown = (yield item), None
                    except GeneratorExit:
                        with call.current():
                            await gen.aclose()
                        call.abort()
                        raise
                    except BaseException as exc:
                        sent, thrown = None, exc
            except BaseException as exc:
                call.fail(exc)
                raise

        result = wraps(func)(async_generator_wrapper)
    elif inspect.isgeneratorfunction(target):

        def generator_wrapper(*args, **kwargs):
            call = _ObservedCall(spec, args, kwargs, streaming=True)
            gen = func(*args, **kwargs)
            sent, thrown = None, None
            try:
                while True:
                    with call.current():
                        try:
                            item = gen.send(sent) if thrown is None else gen.throw(thrown)
                        except StopIteration as stop:
                            call.succeed(stop.value)
                            return stop.value  # noqa: B901
                    call.observe_item(item)
                    try:
                        sent, thrown = (yield item), None
                    except GeneratorExit:
                        with call.current():
                            gen.close()
                        call.abort()
                        raise
                    except BaseException as exc:
                        sent, thrown = None, exc
            except BaseException as exc:
                call.fail(exc)
                raise

        result = wraps(func)(generator_wrapper)
    elif inspect.iscoroutinefunction(target):

        async def coroutine_wrapper(*args, **kwargs):
            call = _ObservedCall(spec, args, kwargs)
            with call.current():
                try:
                    value = await func(*args, **kwargs)
                except BaseException as exc:
                    call.fail(exc)
                    raise
            call.succeed(value)
            return value

        result = wraps(func)(coroutine_wrapper)
    else:

        def function_wrapper(*args, **kwargs):
            call = _ObservedCall(spec, args, kwargs)
            with call.current():
                try:
                    value = func(*args, **kwargs)
                except BaseException as exc:
                    call.fail(exc)
                    raise
            call.succeed(value)
            return value

        result = wraps(func)(function_wrapper)

    result._overmind_observed = True  # type: ignore[attr-defined]
    return result


def _resolve_expectations(items: Iterable[Expectation], default_scope: str) -> tuple[Expectation, ...]:
    resolved: list[Expectation] = []
    for item in items:
        if not isinstance(item, Expectation):
            raise TypeError(f"expectations must be Expectation instances, got {type(item).__name__}")
        resolved.append(item.scoped(default_scope))
    if len(resolved) > MAX_EXPECTATIONS:
        raise ValueError(f"at most {MAX_EXPECTATIONS} expectations per span, got {len(resolved)}")
    ids = [expectation.id for expectation in resolved]
    if len(set(ids)) != len(ids):
        raise ValueError("expectation ids must be unique within one decorator")
    return tuple(resolved)


def observe(
    span_name: str | Callable[..., str] | None = None,
    type: SpanType | str = SpanType.FUNCTION,
    *,
    provenance: str | None = None,
    unit: str | None = None,
    capability: str | None = None,
    capability_id: str | None = None,
    capture: str = "auto",
    ignore: Iterable[str] = (),
    input_key: str | None = None,
    expectations: Iterable[Expectation] = (),
    format_input: Callable[[dict[str, Any]], Any] | None = None,
    format_output: Callable[[Any, dict[str, Any]], Any] | None = None,
    prompt: str | None = None,
) -> Callable[[F], F]:
    """Decorator that traces a function with the full evidence contract.
    Works on plain functions, coroutines, generators and async generators;
    a no-op call-through when the SDK is not initialised.

    A generator's span stays open across its yields but is current only
    while the body runs, so work the consumer does between items is not
    parented to it. Closing the generator early ends the span
    ``overmind.status = "aborted"``.

    Args:
        span_name: Span name; defaults to the function's ``__qualname__``.
            A callable receives each call's arguments and names the span
            per invocation — for polymorphic dispatchers, where the span
            (and a tool span's ``tool.name``) must follow the dispatched
            action, e.g. ``name=lambda self, action, **p: action.name``.
        type: Span type — a :class:`SpanType` or a name like ``"tool"`` /
            ``"llm"`` / ``"entry_point"``.
        provenance: Evidence provenance class (``user`` / ``agent`` /
            ``environment`` / ``harness``); tool, retrieval and LLM spans get
            their natural class automatically.
        unit: ``"run"`` / ``"turn"`` scoring-unit marker; entry points are
            marked ``"run"`` automatically.
        capability: Capability slug or display name to scope this span *and
            its children* to (see :func:`capability`); a differing identity
            mid-trace marks a handoff boundary.
        capability_id: Capability UUID — the recommended identifier, stable
            through renames; the server resolves it before any name. Send it
            with or without ``capability``.
        capture: ``"auto"`` (scrubbed args/result), ``"none"`` (no payloads),
            or ``"messages"`` (normalise the ``messages`` argument and a
            list result — or a streamed generator — into role/content chat
            evidence).
        ignore: Argument names never captured (heavy runtime objects,
            sessions, model handles).
        input_key: Capture only this argument as the input.
        expectations: :class:`~overmind.evals.Expectation` declarations
            emitted on every call. Scope defaults to ``"trace"`` on a unit
            boundary (entry point, ``unit=``) and ``"span"`` elsewhere.
        format_input: Optional ``fn(bound_args) -> payload`` overriding input
            capture.
        format_output: Optional ``fn(result, bound_args) -> payload``
            overriding output capture; for a generator ``result`` is the list
            of yielded items.
        prompt: Literal system/prompt template stamped as
            ``overmind.prompt.template`` for card derivation.
    """
    span_type = _coerce_span_type(type)
    _validate_provenance(provenance)
    _validate_unit(unit)
    if capture not in _CAPTURE_MODES:
        raise ValueError(f"capture must be one of {sorted(_CAPTURE_MODES)}, got {capture!r}")
    if input_key is not None and format_input is not None:
        raise ValueError("input_key and format_input are exclusive")
    ignore_set = frozenset(ignore)
    if input_key is not None and input_key in ignore_set:
        raise ValueError(f"input_key {input_key!r} is also ignored")
    declared = _declared_attributes(span_type, provenance, unit)
    resolved = _resolve_expectations(expectations, "trace" if _boundary_kind(declared) else "span")
    prompt_text = str(prompt) if prompt is not None else None

    def decorator(func: F) -> F:
        signature = _signature(func)
        if input_key is not None and signature is not None:
            parameters = signature.parameters
            accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
            if input_key not in parameters and not accepts_any:
                raise ValueError(f"input_key {input_key!r} is not a parameter of {func!r}")
        target = _target(func)
        spec = _ObserveSpec(
            name=span_name,
            default_name=getattr(target, "__qualname__", None) or repr(func),
            short_name=getattr(target, "__name__", None) or repr(func),
            span_type=span_type,
            declared=declared | code_identity_attributes(func),
            capability=capability,
            capability_id=capability_id,
            capture=capture,
            ignore=ignore_set,
            input_key=input_key,
            format_input=format_input,
            format_output=format_output,
            expectations=resolved,
            signature=signature,
            prompt=prompt_text,
        )
        return _wrap(func, spec)  # type: ignore[return-value]

    return decorator


@contextmanager
def start_span(
    name: str,
    span_type: SpanType | str = SpanType.FUNCTION,
    attributes: Mapping[str, Any] | None = None,
    *,
    provenance: str | None = None,
    unit: str | None = None,
):
    """Context-manager companion to :func:`observe`; stamps the same canonical
    span metadata. Yields a non-recording span when the SDK is uninitialised,
    so instrumentation never crashes an app without an API key."""
    span_type = _coerce_span_type(span_type)
    _validate_provenance(provenance)
    _validate_unit(unit)
    if not _initialized:
        yield trace.INVALID_SPAN
        return
    declared = _declared_attributes(span_type, provenance, unit)
    otel_span = _start_span(name, declared, get_current())
    for key, value in (attributes or {}).items():
        _safe_set_attribute(otel_span, key, value)
    with trace.use_span(otel_span, end_on_exit=False, record_exception=False, set_status_on_exception=False):
        try:
            yield otel_span
        except BaseException as exc:
            _end_span(otel_span, _Outcome.of(exc), exc)
            raise
    if declared.get(attrs.PROVENANCE) == "environment":
        _traces.remember_evidence(otel_span.get_span_context())
    _end_span(otel_span, _Outcome.SUCCESS)


@contextmanager
def _start_child_span(
    name: str,
    *,
    span_type: SpanType = SpanType.FUNCTION,
    provenance: str | None = None,
    attributes: Mapping[str, Any] | None = None,
):
    """Open a span as an explicit child of the current OTel span; re-attaching
    the parent keeps the tree stable across mixed instrumentation stacks."""
    current = trace.get_current_span()
    token = None
    try:
        if current is not None and current.get_span_context().is_valid:
            token = attach(trace.set_span_in_context(current))
        with start_span(name, span_type=span_type, provenance=provenance, attributes=attributes) as span:
            yield span
    finally:
        if token is not None:
            detach(token)


def entry_point(name: str | None = None, **kwargs) -> Callable[[F], F]:
    """Decorator that traces an entry point span (marked ``unit_kind = "run"``)."""
    return observe(span_name=name, type=SpanType.ENTRY_POINT, **kwargs)


def workflow(name: str | None = None, **kwargs) -> Callable[[F], F]:
    """Decorator that traces a workflow span."""
    return observe(span_name=name, type=SpanType.WORKFLOW, **kwargs)


def tool(name: str | Callable[..., str] | None = None, **kwargs) -> Callable[[F], F]:
    """Decorator that traces a tool span (adds ``tool.name``; inside
    :func:`tool_call` also ``gen_ai.tool.call.id`` and a link to the
    requesting LLM span).

    ``name`` may be a callable receiving each call's arguments, so a
    polymorphic dispatcher emits per-action tool spans from one decoration:
    ``@tool(name=lambda self, action, **p: action)``. The span name and
    ``tool.name`` both follow the resolved value."""
    return observe(span_name=name, type=SpanType.TOOL, **kwargs)


def retrieval(name: str | None = None, **kwargs) -> Callable[[F], F]:
    """Decorator that traces a retrieval / RAG step span."""
    return observe(span_name=name, type=SpanType.RETRIEVAL, **kwargs)


def _grounding_span_id(handle: Any) -> str:
    """Accept a span_id hex string or any OTel span handle (e.g. the span
    yielded by :func:`start_span`)."""
    if isinstance(handle, str):
        return handle
    return format(handle.get_span_context().span_id, "016x")


def deliver(
    payload: Any,
    *,
    grounded_by: list[Any] | None = None,
    name: str = "deliver",
    provenance: str = "agent",
) -> None:
    """Capture the terminal deliverable of a run on its own child span:
    the payload is serialised into ``outputs`` and the span carries
    ``overmind.delivery = true``.

    ``grounded_by`` names the evidence spans the deliverable rests on
    (span_id hex strings or span handles). When omitted, the environment-
    provenance spans the SDK collected for the current trace are used —
    call inside the run so the trace is still active."""
    if not _initialized:
        return
    _validate_provenance(provenance)
    if grounded_by is None:
        span_context = trace.get_current_span().get_span_context()
        grounded_by = _traces.take_evidence(span_context.trace_id) if span_context.is_valid else []
    with _start_child_span(name, provenance=provenance, attributes={attrs.DELIVERY: True}) as otel_span:
        if grounded_by:
            otel_span.set_attribute(attrs.GROUNDED_BY, json.dumps([_grounding_span_id(h) for h in grounded_by]))
        _set_payload(otel_span, "outputs", payload)


# Crossing a boundary that does not copy contextvars (threads, queues,
# subprocesses). The carried context values are the OTel context keys the
# on-start processor reads, so continuing a trace is a straight copy.
_PROPAGATOR = CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
_CARRIED_KEYS = (
    attrs.CAPABILITY_ID,
    attrs.CAPABILITY_NAME,
    attrs.CAPABILITY_SLUG,
    attrs.PROJECT_ID,
    attrs.BEHAVIOUR_KEY,
    attrs.WORKFLOW_NAME,
    attrs.CONVERSATION_ID,
)


def carrier() -> dict[str, str]:
    """The current trace as header-safe strings (``traceparent``, ``baggage``
    with the capability identity, behaviour key, conversation and step), for
    a queue message, HTTP headers or a subprocess environment. ``{}`` when
    no span is current."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    ctx = get_current()
    for key in _CARRIED_KEYS:
        if (value := get_value(key)) is not None:
            ctx = baggage.set_baggage(key, str(value), ctx)
    ctx = baggage.set_baggage(attrs.STEP, str(_traces.current_step(span_context.trace_id)), ctx)
    out: dict[str, str] = {}
    _PROPAGATOR.inject(out, ctx)
    return out


def _remote_context(carrier: Mapping[str, str]) -> Context:
    """Decode a carrier (keys case-insensitive, so ``os.environ`` works) on
    top of the current context, copying the carried values back onto their
    context keys and witnessing the remote step. A carrier without a
    traceparent leaves the current context as it is."""
    lowered = {str(k).lower(): str(v) for k, v in carrier.items()}
    ctx = _PROPAGATOR.extract(lowered, get_current())
    carried = baggage.get_all(ctx)
    for key in _CARRIED_KEYS:
        if key in carried:
            ctx = set_value(key, carried[key], ctx)
    span_context = trace.get_current_span(ctx).get_span_context()
    if span_context.is_valid and str(carried.get(attrs.STEP, "")).isdigit():
        _traces.witness_step(span_context.trace_id, int(carried[attrs.STEP]))
    return ctx


@contextmanager
def continue_trace(carrier: Mapping[str, str]) -> Iterator[None]:
    """Make spans inside this block continue the trace in *carrier* — the
    primitive for threads, Celery task bodies and any hop that does not copy
    contextvars (``asyncio.create_task`` already does). An empty or invalid
    carrier is a no-op block."""
    token = attach(_remote_context(carrier))
    try:
        yield
    finally:
        detach(token)


def _attach_environment_carrier() -> None:
    """A subprocess started with ``env={**os.environ, **carrier()}`` — or the
    optimiser's ``TRACEPARENT`` — continues the parent trace for its lifetime."""
    env = {key.lower(): value for key, value in os.environ.items()}
    if "traceparent" not in env and "otel_traceparent" in env:
        env["traceparent"] = env["otel_traceparent"]
    if "traceparent" in env:
        attach(_remote_context(env))


def flush_traces(timeout_millis: int = 1000) -> None:
    """Best-effort exporter flush; no-op if the provider lacks ``force_flush``.
    Touches no turn spans — safe while other runs are live in the process."""
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=timeout_millis)


def force_flush_traces(timeout_millis: int = 1000) -> None:
    """Process-exit flush: ends every still-open turn span first, so a run
    that never closed its boundary span still exports its units. Inside a
    live process prefer :func:`flush_traces` — this one truncates other
    concurrent runs' turns."""
    _turn_registry.end_all()
    flush_traces(timeout_millis=timeout_millis)


__all__ = [
    "SpanType",
    "capability",
    "capture_exception",
    "carrier",
    "code_identity_attributes",
    "continue_trace",
    "deliver",
    "enable_tracing",
    "entry_point",
    "env_identity",
    "flush_traces",
    "force_flush_traces",
    "get_api_settings",
    "get_tracer",
    "init",
    "observe",
    "retrieval",
    "set_conversation_id",
    "set_tag",
    "set_user",
    "set_workflow_name",
    "start_span",
    "task",
    "tool",
    "tool_call",
    "workflow",
]
