"""Span attribute keys emitted by the Overmind CLI.

Mirrored from ``overmind/attrs.py`` in the claw repository — a key the server
interprets must be added there first and copied here verbatim.
"""

from __future__ import annotations

STATUS = "overmind.status"  # "running" | "success" | "failed" | "cancelled"
ERROR_TYPE = "overmind.error.type"
ERROR_MESSAGE = "overmind.error.message"
PROGRESS_PHASE = "overmind.progress.phase"
PROGRESS_CURRENT = "overmind.progress.current"
PROGRESS_TOTAL = "overmind.progress.total"
DURATION_SECONDS = "overmind.duration.seconds"

# Current SDKs stamp the bare key; older Python builds used the namespaced one.
CONVERSATION_ID = "conversation.id"
CONVERSATION_ID_LEGACY = "overmind.conversation.id"

CAPABILITY_ID = "overmind.capability.id"
PROJECT_ID = "overmind.project.id"
JOB_ID = "overmind.job.id"

CAPABILITY_NAME = "overmind.capability.name"

# Span EVENT names / event-attribute keys, not span attributes; payloads are JSON strings.
EVAL_SCHEMA_VERSION = "overmind.eval.schema_version"
EVAL_PAYLOAD = "overmind.eval.payload"
EVAL_EVENT_EXPECTATION = "overmind.eval.expectation"
EVAL_EVENT_CONTEXT = "overmind.eval.context"
EVAL_EVENT_CHECKPOINT = "overmind.eval.checkpoint"
EVAL_EVENT_CONVERSATION_END = "overmind.eval.conversation_end"
EVAL_EVENT_INTENT = "overmind.eval.intent"

# Stamped on LLM spans by ``opentelemetry-overmind`` when a PromptString is used.
PROMPT_TEMPLATE = "overmind.prompt.template"
PROMPT_KWARGS = "overmind.prompt.kwargs"  # JSON string

LLM_MODEL = "genai.model"
LLM_RESPONSE_MODEL = "genai.response.model"
LLM_PROVIDER = "genai.provider"
LLM_PROMPT_TOKENS = "genai.prompt_tokens"
LLM_COMPLETION_TOKENS = "genai.completion_tokens"
LLM_TOTAL_TOKENS = "genai.total_tokens"
# Never folded into ``total_tokens`` (docs/tracing-attributes.md §3).
LLM_CACHE_READ_TOKENS = "genai.cache_read_tokens"
LLM_COST = "genai.cost"
LLM_ERROR = "genai.error"
LLM_ELAPSED_SECONDS = "genai.elapsed_seconds"
LLM_REQUEST_MESSAGE_COUNT = "genai.request.message_count"
LLM_REQUEST_MESSAGE_CHARS = "genai.request.message_chars"
LLM_REQUEST_TOOL_COUNT = "genai.request.tool_count"
LLM_REQUEST_KWARGS = "genai.request.kwargs"
LLM_REQUEST_TEMPERATURE = "genai.request.temperature"
LLM_REQUEST_MAX_TOKENS = "genai.request.max_tokens"
LLM_REQUEST_TOP_P = "genai.request.top_p"
LLM_RESPONSE_MESSAGE_CHARS = "genai.response.message_chars"
LLM_RESPONSE_FINISH_REASON = "genai.response.finish_reason"
LLM_STREAMING = "genai.streaming"
LLM_TTFT_SECONDS = "genai.time_to_first_token_seconds"
LLM_USAGE_PROMPT_TOKENS = "genai.usage.prompt_tokens"
LLM_USAGE_COMPLETION_TOKENS = "genai.usage.completion_tokens"
LLM_USAGE_TOTAL_TOKENS = "genai.usage.total_tokens"

TOOL_NAME = "tool.name"
TOOL_ARG_KEYS = "tool.arg_keys"
TOOL_ERROR = "tool.error"

RETRIEVAL_QUERY_CHARS = "overmind.retrieval.query_chars"
RETRIEVAL_RESULT_COUNT = "overmind.retrieval.result_count"

PROVENANCE = "overmind.provenance"  # user | agent | environment | harness
UNIT_KIND = "overmind.unit_kind"  # turn | run
DELIVERY = "overmind.delivery"  # true on the span carrying the terminal deliverable

INPUT_DATA = "overmind.input.data"
OUTPUT_DATA = "overmind.output.data"
SCORE = "overmind.score"
ERROR = "overmind.error"
SPAN_TYPE = "overmind.span.type"
