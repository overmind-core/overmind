"""Span attribute / tag keys used across Overmind.

Every tag attached to an Overmind trace span (via ``overmind.set_tag`` or
``start_span(..., attributes=...)``) is defined here so the schema of
attributes we emit is auditable from a single file.  Add new tags here
first, then import the constant at the call site — never inline a raw
``"overmind.*"`` / ``"gen_ai.*"`` / ``"tool.*"`` string elsewhere.

Naming rule
-----------
Keys under ``overmind.*`` use dotted segments
(``overmind.capability.id``, ``overmind.error.type``, …); never a single
underscore-blob like ``overmind.capability_id``.

Keep ``overbae/api/overmind_attrs.py`` in lockstep with this file when
adding or renaming keys.
"""

from __future__ import annotations

# Generic lifecycle tags — explicit status / progress markers we set on any
# span so the platform can surface "what's happening?" without parsing the
# OTel ``status_code`` directly.
STATUS = "overmind.status"  # "running" | "success" | "failed" | "cancelled"
ERROR_TYPE = "overmind.error.type"  # exception class name, when STATUS == "failed"
ERROR_MESSAGE = "overmind.error.message"  # short, scrubbed message
PROGRESS_PHASE = "overmind.progress.phase"  # human label for current pipeline step
PROGRESS_CURRENT = "overmind.progress.current"  # current step index (1-based)
PROGRESS_TOTAL = "overmind.progress.total"  # total number of steps
DURATION_SECONDS = "overmind.duration.seconds"  # walltime of the wrapped span

# Top-level resource / context tags (link spans to server-side entities).
#
# These are the identity keys the Overmind server uses to attach a span to a
# concrete row.  ``overmind.capability.id`` is the mapping key: ingest binds
# spans to tasks, trajectories, and behaviours by this UUID alone (resource
# attributes OR any span attribute).  ``overmind.capability.name`` is an
# accessibility label and never resolves a capability.
CAPABILITY_ID = "overmind.capability.id"
PROJECT_ID = "overmind.project.id"
JOB_ID = "overmind.job.id"

CAPABILITY_NAME = "overmind.capability.name"
# Tracelo­op-compatible workflow label attached to every downstream span by
# the on-start processor.  Stored in the OTel context so child spans pick
# it up automatically.
WORKFLOW_NAME = "overmind.workflow.name"
# Stable per-conversation identifier used by chat agents to group multiple
# traces under the same user-visible session.
CONVERSATION_ID = "overmind.conversation.id"

# Runtime eval envelope (overmind/evals.py) — SPAN EVENT names plus the two
# attributes every envelope event carries.  These are wire contract v1: the
# platform parses them server-side (see docs/tracing-attributes.md §6), so
# the values are pinned in tests/test_attrs.py — never rename.
EVAL_EXPECTATION_EVENT = "overmind.eval.expectation"
EVAL_CONTEXT_EVENT = "overmind.eval.context"
EVAL_CHECKPOINT_EVENT = "overmind.eval.checkpoint"
EVAL_CONVERSATION_END_EVENT = "overmind.eval.conversation_end"
EVAL_INTENT_EVENT = "overmind.eval.intent"
EVAL_SCHEMA_VERSION = "overmind.eval.schema_version"  # int, currently 1
EVAL_PAYLOAD = "overmind.eval.payload"  # JSON-serialized payload string

# Evaluation evidence contract — the platform's judges read these exact keys
# (pinned in tests/test_evidence_contract.py; see docs/tracing-attributes.md
# §7).  ``unit_kind`` / ``grounded_by`` deliberately break the dotted-segments
# naming rule above: the platform ingest is built against these literal keys.
PROVENANCE = "overmind.provenance"  # "user" | "agent" | "environment" | "harness"
UNIT_KIND = "overmind.unit_kind"  # "turn" | "run" (ATSC agent-turn vocabulary)
DELIVERY = "overmind.delivery"  # true on the span carrying the terminal deliverable
GROUNDED_BY = "overmind.grounded_by"  # JSON array of span_id hex strings
# Declared Behaviour.slug. Optional; the server binds structurally without it.
BEHAVIOUR_KEY = "overmind.behaviour.key"
# Live scoring reads these off the span: the nearest declared intent up the
# ancestry, one-sentence expectations judged against this span's output, and
# the tool names the span's subtree is expected to call, in order.
INTENT = "overmind.intent"
EXPECTATIONS = "overmind.expectations"
REFERENCE_TRAJECTORY = "overmind.reference_trajectory"

# LLM call metadata — CANONICAL ``genai.*`` contract
#
# These are the keys the Overmind server actually reads (see
# ``overbae/api/overmind_attrs.py`` + ``overbae/api/otlp.py::_build_span_usage``).
# The server rolls token usage + cost up onto the Agent from these keys, so the
# SDK MUST emit them.  Token counts use ``prompt_tokens`` / ``completion_tokens``
# (NOT the OTel semconv ``input_tokens`` / ``output_tokens``).
#
# The SDK ALSO emits the OTel semconv ``gen_ai.*`` keys below (see
# ``OTEL_*``) so third-party consumers keep working, and the on-end enrichment processor mirrors any ``gen_ai.*``
# usage produced by auto-instrumentors into these canonical keys.
LLM_MODEL = "genai.model"
LLM_RESPONSE_MODEL = "genai.response.model"
LLM_PROVIDER = "genai.provider"
LLM_ERROR = "genai.error"
LLM_ELAPSED_SECONDS = "genai.elapsed_seconds"
LLM_REQUEST_MESSAGE_COUNT = "genai.request.message_count"
LLM_REQUEST_MESSAGE_CHARS = "genai.request.message_chars"
LLM_REQUEST_TOOL_COUNT = "genai.request.tool_count"
LLM_REQUEST_KWARGS = "genai.request.kwargs"
# Request parameters (server does not roll these up yet — see contract doc).
LLM_REQUEST_TEMPERATURE = "genai.request.temperature"
LLM_REQUEST_MAX_TOKENS = "genai.request.max_tokens"
LLM_REQUEST_TOP_P = "genai.request.top_p"
# Response shape / streaming telemetry.
LLM_RESPONSE_MESSAGE_CHARS = "genai.response.message_chars"
LLM_RESPONSE_FINISH_REASON = "genai.response.finish_reason"
LLM_STREAMING = "genai.streaming"
LLM_TTFT_SECONDS = "genai.time_to_first_token_seconds"
# Token usage — canonical (rolled up by the server).
LLM_PROMPT_TOKENS = "genai.prompt_tokens"
LLM_COMPLETION_TOKENS = "genai.completion_tokens"
LLM_TOTAL_TOKENS = "genai.total_tokens"
LLM_CACHE_READ_TOKENS = "genai.cache_read_tokens"
# Cost in USD.  Taken from the provider (``usage.cost`` / hidden params) when
# present, else computed from model pricing.  Rolled up by the server.
LLM_COST = "genai.cost"
# ``genai.usage.*`` aliases the server also accepts for tokens.  We emit the
# short ``genai.prompt_tokens`` form above; these exist so the mirror helper
# and contract stay explicit about both accepted shapes.
LLM_USAGE_PROMPT_TOKENS = "genai.usage.prompt_tokens"
LLM_USAGE_COMPLETION_TOKENS = "genai.usage.completion_tokens"
LLM_USAGE_TOTAL_TOKENS = "genai.usage.total_tokens"

# OpenTelemetry GenAI semantic-convention keys.
#
# Emitted ALONGSIDE the canonical ``genai.*`` keys above so OTel-native
# consumers keep resolving model + tokens.  Never read these on the server —
# read the canonical ``genai.*`` keys instead.
OTEL_LLM_REQUEST_MODEL = "gen_ai.request.model"
OTEL_LLM_SYSTEM = "gen_ai.system"
OTEL_LLM_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
OTEL_LLM_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
OTEL_LLM_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"

# OTel code semantic-convention keys — stamped by ``observe()`` on every
# decorated span so the server can bind spans to Behaviour Registry anchors
# (``module.qualname``).  ``code.function.name`` is the stable semconv key
# (carries ``__qualname__``); ``code.namespace`` is deprecated upstream but
# kept so module and qualname stay separately queryable server-side.
CODE_NAMESPACE = "code.namespace"
CODE_FUNCTION_NAME = "code.function.name"

# Tool-call metadata (server reads these to classify + attribute tool spans).
TOOL_NAME = "tool.name"
TOOL_ARG_KEYS = "tool.arg_keys"
TOOL_ERROR = "tool.error"

# Retrieval / RAG step metadata (namespaced under ``overmind.retrieval.*``;
# server does not roll these up yet — see contract doc).
RETRIEVAL_QUERY_CHARS = "overmind.retrieval.query_chars"
RETRIEVAL_RESULT_COUNT = "overmind.retrieval.result_count"

# Resource-level enrichment (set once on the OTel Resource in ``init``).
SDK_NAME = "overmind.sdk.name"
SDK_VERSION = "overmind.sdk.version"
# Commit sha of the running code (OTel VCS semconv), auto-detected in
# ``init()`` from env vars or ``.git/HEAD``.  Omitted when undetectable.
VCS_REF_HEAD_REVISION = "vcs.ref.head.revision"

# Span-level classification / error summary
# Human-readable summary string attached when a span fails. ``ERROR_TYPE``
# and ``ERROR_MESSAGE`` carry the structured (class name + scrubbed
# message) pair; ``ERROR_SUMMARY`` is the free-form line the UI renders.
ERROR_SUMMARY = "overmind.error"
# Explicit span-type override — set to SpanType values when the heuristic
# classification would be wrong (e.g. "entry_point", "tool", "workflow").
SPAN_TYPE = "overmind.span.type"
