/** Overmind's canonical span attribute contract; mirrors `overmind/overmind/attrs.py`. */
export const OvermindAttributes = {
  CAPABILITY_ID: "overmind.capability.id",
  CAPABILITY_NAME: "overmind.capability.name",
  PROJECT_ID: "overmind.project.id",
  WORKFLOW_NAME: "overmind.workflow.name",
  CONVERSATION_ID: "conversation.id",
  SPAN_TYPE: "overmind.span.type",
  STATUS: "overmind.status",
  ERROR_MESSAGE: "overmind.error.message",
  ERROR_TYPE: "overmind.error.type",
  DURATION_SECONDS: "overmind.duration.seconds",
  SDK_NAME: "overmind.sdk.name",
  SDK_VERSION: "overmind.sdk.version",
  ENVIRONMENT: "overmind.environment",
  RELEASE: "overmind.release",
  VERSION: "overmind.version",
  PROMPT_NAME: "overmind.prompt.name",
  PROMPT_VERSION: "overmind.prompt.version",
  TRACE_TAGS: "overmind.trace.tags",
  INTENT: "overmind.intent",
  EXPECTATIONS: "overmind.expectations",
  REFERENCE_TRAJECTORY: "overmind.reference_trajectory",

  INPUTS: "inputs",
  OUTPUTS: "outputs",

  LLM_MODEL: "genai.model",
  LLM_REQUEST_TEMPERATURE: "genai.request.temperature",
  LLM_REQUEST_MAX_TOKENS: "genai.request.max_tokens",
  LLM_REQUEST_TOP_P: "genai.request.top_p",
  LLM_REQUEST_KWARGS: "genai.request.kwargs",
  LLM_PROMPT_TOKENS: "genai.prompt_tokens",
  LLM_COMPLETION_TOKENS: "genai.completion_tokens",
  LLM_TOTAL_TOKENS: "genai.total_tokens",
  LLM_CACHE_READ_TOKENS: "genai.cache_read_tokens",
  LLM_COST: "genai.cost",
  LLM_TTFT_SECONDS: "genai.time_to_first_token_seconds",

  OTEL_LLM_REQUEST_MODEL: "gen_ai.request.model",
  OTEL_LLM_USAGE_PROMPT_TOKENS: "gen_ai.usage.prompt_tokens",
  OTEL_LLM_USAGE_COMPLETION_TOKENS: "gen_ai.usage.completion_tokens",
  OTEL_LLM_USAGE_TOTAL_TOKENS: "gen_ai.usage.total_tokens",

  TOOL_NAME: "tool.name",
  TOOL_ARG_KEYS: "tool.arg_keys",
  RETRIEVAL_QUERY_CHARS: "overmind.retrieval.query_chars",
  RETRIEVAL_RESULT_COUNT: "overmind.retrieval.result_count",
} as const;

export const OvermindSpanType = {
  LLM_CALL: "llm_call",
  TOOL_CALL: "tool_call",
  RETRIEVAL: "retrieval",
  WORKFLOW: "workflow",
  FUNCTION: "function",
} as const;

export type OvermindSpanType = (typeof OvermindSpanType)[keyof typeof OvermindSpanType];
