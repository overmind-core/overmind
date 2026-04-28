"""Span attribute / tag keys used across OverClaw.

Every tag attached to an OverClaw trace span (via ``overmind.set_tag`` or
``start_span(..., attributes=...)``) is defined here so the schema of
attributes we emit is auditable from a single file.  Add new tags here
first, then import the constant at the call site — never inline a raw
``"overclaw.*"`` / ``"llm.*"`` / ``"tool.*"`` string elsewhere.

Constants are grouped by the subsystem that emits them so it's easy to
see which area of the codebase produces which tags.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Top-level command tag (set on every CLI entry-point span).
# ---------------------------------------------------------------------------
COMMAND = "overclaw.command"

# ---------------------------------------------------------------------------
# Agent registry / `overclaw agent ...` commands
# (overclaw/commands/agent_cmd.py)
# ---------------------------------------------------------------------------
AGENT_NAME = "overclaw.agent.name"
AGENT_ENTRYPOINT = "overclaw.agent.entrypoint"
AGENT_NEW_ENTRYPOINT = "overclaw.agent.new_entrypoint"
AGENT_OLD_ENTRYPOINT = "overclaw.agent.old_entrypoint"
AGENT_FILE_PATH = "overclaw.agent.file_path"
AGENT_FUNCTION_NAME = "overclaw.agent.function_name"
AGENT_REGISTERED_COUNT = "overclaw.agent.registered_count"
AGENT_REMOVED = "overclaw.agent.removed"
AGENT_FILE_EXISTS = "overclaw.agent.file_exists"
AGENT_SETUP_SPEC_READY = "overclaw.agent.setup_spec_ready"
AGENT_EXPERIMENT_FILE_COUNT = "overclaw.agent.experiment_file_count"

# ---------------------------------------------------------------------------
# `overclaw init` (overclaw/commands/init_cmd.py)
# ---------------------------------------------------------------------------
INIT_ENV_PATH = "overclaw.init.env_path"
INIT_HAS_OPENAI_KEY = "overclaw.init.has_openai_key"
INIT_HAS_ANTHROPIC_KEY = "overclaw.init.has_anthropic_key"
INIT_HAS_OVERMIND_TOKEN = "overclaw.init.has_overmind_token"
INIT_ANALYZER_MODEL = "overclaw.init.analyzer_model"
INIT_HAS_SYNTHETIC_DATAGEN_MODEL = "overclaw.init.has_synthetic_datagen_model"

# ---------------------------------------------------------------------------
# `overclaw doctor` (overclaw/commands/doctor_cmd.py)
# ---------------------------------------------------------------------------
DOCTOR_AGENT_NAME = "overclaw.doctor.agent_name"
DOCTOR_BUNDLE_BUILT = "overclaw.doctor.bundle_built"
DOCTOR_BUNDLE_FILES = "overclaw.doctor.bundle_files"
DOCTOR_BUNDLE_RAW_CHARS = "overclaw.doctor.bundle_raw_chars"
DOCTOR_BUNDLE_PROMPT_CHARS = "overclaw.doctor.bundle_prompt_chars"
DOCTOR_HAS_EVAL_SPEC = "overclaw.doctor.has_eval_spec"
DOCTOR_HAS_INSTRUMENTED_COPY = "overclaw.doctor.has_instrumented_copy"

# ---------------------------------------------------------------------------
# `overclaw setup` (overclaw/commands/setup_cmd.py +
# overclaw/setup/{questionnaire,policy_generator}.py)
# ---------------------------------------------------------------------------
SETUP_AGENT_NAME = "overclaw.setup.agent_name"
SETUP_FAST = "overclaw.setup.fast"
SETUP_MODEL = "overclaw.setup.model"
SETUP_HAS_POLICY = "overclaw.setup.has_policy"
SETUP_HAS_SEED_DATA = "overclaw.setup.has_seed_data"
SETUP_POLICY_PATH = "overclaw.setup.policy_path"
SETUP_DATA_PATH = "overclaw.setup.data_path"
SETUP_STORAGE_BACKEND = "overclaw.setup.storage_backend"
SETUP_AGENT_PATH = "overclaw.setup.agent_path"
SETUP_ENTRYPOINT_FN = "overclaw.setup.entrypoint_fn"
SETUP_ANALYZER_MODEL = "overclaw.setup.analyzer_model"
SETUP_PHASE = "overclaw.setup.phase"
SETUP_EVAL_SPEC_FIELD_COUNT = "overclaw.setup.eval_spec_field_count"
SETUP_EVAL_SPEC_HAS_TOOLS = "overclaw.setup.eval_spec_has_tools"
SETUP_EVAL_SPEC_HAS_JUDGE = "overclaw.setup.eval_spec_has_judge"
SETUP_EVAL_SPEC_HAS_POLICY = "overclaw.setup.eval_spec_has_policy"
SETUP_EVAL_SPEC_STRUCTURE_WEIGHT = "overclaw.setup.eval_spec_structure_weight"
SETUP_EVAL_SPEC_TOOL_COUNT = "overclaw.setup.eval_spec_tool_count"
SETUP_EVAL_SPEC_CONSISTENCY_RULE_COUNT = (
    "overclaw.setup.eval_spec_consistency_rule_count"
)
SETUP_DATASET_SOURCE = "overclaw.setup.dataset_source"
SETUP_DATASET_ID = "overclaw.setup.dataset_id"
SETUP_CRITERIA_SOURCE = "overclaw.setup.criteria_source"
SETUP_POLICY_SOURCE = "overclaw.setup.policy_source"
SETUP_AGENT_POLICY_MARKDOWN = "overclaw.agent_policy_markdown"
SETUP_AGENT_POLICY_DATA = "overclaw.agent_policy_data"

# ---------------------------------------------------------------------------
# `overclaw optimize` (overclaw/commands/optimize_cmd.py)
# ---------------------------------------------------------------------------
OPTIMIZE_AGENT_NAME = "overclaw.optimize.agent_name"
OPTIMIZE_AGENT_PATH = "overclaw.optimize.agent_path"
OPTIMIZE_FAST = "overclaw.optimize.fast"
OPTIMIZE_ENTRYPOINT_FN = "overclaw.optimize.entrypoint_fn"
OPTIMIZE_STORAGE_BACKEND = "overclaw.optimize.storage_backend"
OPTIMIZE_ANALYZER_MODEL = "overclaw.optimize.analyzer_model"
OPTIMIZE_LLM_JUDGE_MODEL = "overclaw.optimize.llm_judge_model"
OPTIMIZE_ITERATIONS = "overclaw.optimize.iterations"
OPTIMIZE_CANDIDATES_PER_ITERATION = "overclaw.optimize.candidates_per_iteration"
OPTIMIZE_PARALLEL = "overclaw.optimize.parallel"
OPTIMIZE_MAX_WORKERS = "overclaw.optimize.max_workers"
OPTIMIZE_RUNS_PER_EVAL = "overclaw.optimize.runs_per_eval"
OPTIMIZE_REGRESSION_THRESHOLD = "overclaw.optimize.regression_threshold"
OPTIMIZE_HOLDOUT_RATIO = "overclaw.optimize.holdout_ratio"
OPTIMIZE_HOLDOUT_ENFORCEMENT = "overclaw.optimize.holdout_enforcement"
OPTIMIZE_EARLY_STOPPING_PATIENCE = "overclaw.optimize.early_stopping_patience"
OPTIMIZE_CROSS_RUN_PERSISTENCE = "overclaw.optimize.cross_run_persistence"
OPTIMIZE_FAILURE_CLUSTERING = "overclaw.optimize.failure_clustering"
OPTIMIZE_ADAPTIVE_FOCUS = "overclaw.optimize.adaptive_focus"
OPTIMIZE_MODEL_BACKTESTING = "overclaw.optimize.model_backtesting"
OPTIMIZE_BACKTEST_MODELS = "overclaw.optimize.backtest_models"
OPTIMIZE_EVAL_SPEC_PATH = "overclaw.optimize.eval_spec_path"
OPTIMIZE_DATA_PATH = "overclaw.optimize.data_path"

# Optimizer pipeline (overclaw/optimize/optimizer.py) — runtime tags emitted
# from the optimizer's spans during a run.
OPTIMIZE_PHASE = "overclaw.optimize.phase"
OPTIMIZE_DATASET_TOTAL = "overclaw.optimize.dataset_total"
OPTIMIZE_DATASET_TRAIN = "overclaw.optimize.dataset_train"
OPTIMIZE_DATASET_HOLDOUT = "overclaw.optimize.dataset_holdout"
OPTIMIZE_BASELINE_SCORE = "overclaw.optimize.baseline_score"
OPTIMIZE_RUN_NAME = "overclaw.optimize.run_name"
OPTIMIZE_ITERATION = "overclaw.optimize.iteration"
OPTIMIZE_TOTAL_ITERATIONS = "overclaw.optimize.total_iterations"
OPTIMIZE_BEST_SCORE_BEFORE = "overclaw.optimize.best_score_before"
OPTIMIZE_BEST_SCORE_AFTER = "overclaw.optimize.best_score_after"
OPTIMIZE_STALL_COUNT = "overclaw.optimize.stall_count"
OPTIMIZE_TEMPERATURE = "overclaw.optimize.temperature"
OPTIMIZE_N_CANDIDATES_GENERATED = "overclaw.optimize.n_candidates_generated"
OPTIMIZE_N_CANDIDATES_VALID = "overclaw.optimize.n_candidates_valid"
OPTIMIZE_CANDIDATE_INDEX = "overclaw.optimize.candidate_index"
OPTIMIZE_CANDIDATE_METHOD = "overclaw.optimize.candidate_method"
OPTIMIZE_CANDIDATE_SCORE = "overclaw.optimize.candidate_score"
OPTIMIZE_CANDIDATE_ADJUSTED_SCORE = "overclaw.optimize.candidate_adjusted_score"
OPTIMIZE_COMPLEXITY_PENALTY = "overclaw.optimize.complexity_penalty"
OPTIMIZE_DATA_LEAKAGE_COUNT = "overclaw.optimize.data_leakage_count"
OPTIMIZE_REGRESSION_FAILURES = "overclaw.optimize.regression_failures"
OPTIMIZE_ITERATION_DECISION = "overclaw.optimize.iteration_decision"
OPTIMIZE_ITERATION_SCORE = "overclaw.optimize.iteration_score"
OPTIMIZE_ITERATION_IMPROVEMENT = "overclaw.optimize.iteration_improvement"
OPTIMIZE_ITERATION_REASON = "overclaw.optimize.iteration_reason"
OPTIMIZE_ACCEPTED = "overclaw.optimize.accepted"
OPTIMIZE_FINAL_BEST_SCORE = "overclaw.optimize.final_best_score"
OPTIMIZE_HOLDOUT_SCORE = "overclaw.optimize.holdout_score"
OPTIMIZE_HOLDOUT_BASELINE_SCORE = "overclaw.optimize.holdout_baseline_score"
OPTIMIZE_HOLDOUT_IMPROVEMENT = "overclaw.optimize.holdout_improvement"
OPTIMIZE_BLENDED_IMPROVEMENT = "overclaw.optimize.blended_improvement"
OPTIMIZE_HOLDOUT_REVERTED = "overclaw.optimize.holdout_reverted"
OPTIMIZE_BACKTEST_MODEL = "overclaw.optimize.backtest_model"
OPTIMIZE_BACKTEST_SCORE = "overclaw.optimize.backtest_score"

# ---------------------------------------------------------------------------
# Evaluator (overclaw/optimize/evaluator.py)
# ---------------------------------------------------------------------------
EVAL_BATCH_SIZE = "overclaw.eval.batch_size"
EVAL_AVG_TOTAL = "overclaw.eval.avg_total"
EVAL_USED_LLM_JUDGE = "overclaw.eval.used_llm_judge"

# ---------------------------------------------------------------------------
# Cross-run optimization state (overclaw/optimize/run_state.py)
# ---------------------------------------------------------------------------
RUN_STATE_TOTAL_RUNS = "overclaw.run_state.total_runs"
RUN_STATE_REGRESSION_CASES = "overclaw.run_state.regression_cases"
RUN_STATE_FAILURE_CLUSTERS = "overclaw.run_state.failure_clusters"
RUN_STATE_LATEST_BASELINE = "overclaw.run_state.latest_baseline"
RUN_STATE_LATEST_FINAL = "overclaw.run_state.latest_final"
RUN_STATE_LATEST_ACCEPTED = "overclaw.run_state.latest_accepted"
RUN_STATE_LATEST_REJECTED = "overclaw.run_state.latest_rejected"

# ---------------------------------------------------------------------------
# Candidate generation (overclaw/optimize/analyzer.py)
# ---------------------------------------------------------------------------
CANDIDATES_REQUESTED = "overclaw.candidates.requested"
CANDIDATES_PRODUCED = "overclaw.candidates.produced"
CANDIDATES_METHODS = "overclaw.candidates.methods"
CANDIDATES_HAS_DIAGNOSIS = "overclaw.candidates.has_diagnosis"
CANDIDATES_USE_BUNDLE = "overclaw.candidates.use_bundle"
CANDIDATES_HAS_ROOT_CAUSE = "overclaw.candidates.has_root_cause"
CANDIDATES_FALLBACK = "overclaw.candidates.fallback"

# ---------------------------------------------------------------------------
# Synthetic data generation (overclaw/optimize/data.py)
# ---------------------------------------------------------------------------
DATAGEN_MODE = "overclaw.datagen.mode"
DATAGEN_MODEL = "overclaw.datagen.model"
DATAGEN_REQUESTED_SAMPLES = "overclaw.datagen.requested_samples"
DATAGEN_GENERATED_COUNT = "overclaw.datagen.generated_count"
DATAGEN_PERSONA_COUNT = "overclaw.datagen.persona_count"
DATAGEN_PERSONA_SOURCE = "overclaw.datagen.persona_source"
DATAGEN_PERSONA_IDX = "overclaw.datagen.persona_idx"
DATAGEN_PERSONA_NAME = "overclaw.datagen.persona_name"
DATAGEN_PERSONA_INTENT = "overclaw.datagen.persona_intent"
DATAGEN_PERSONA_SHARDS = "overclaw.datagen.persona_shards"
DATAGEN_RETRY_ATTEMPT = "overclaw.datagen.retry_attempt"
DATAGEN_RETRY_MAX_ATTEMPTS = "overclaw.datagen.retry_max_attempts"
DATAGEN_ANTI_EXAMPLES = "overclaw.datagen.anti_examples"
DATAGEN_ROUND = "overclaw.datagen.round"
DATAGEN_ROUNDS = "overclaw.datagen.rounds"
DATAGEN_TARGET = "overclaw.datagen.target"
DATAGEN_HAVE_BEFORE = "overclaw.datagen.have_before"
DATAGEN_ELAPSED_SECONDS = "overclaw.datagen.elapsed_seconds"
DATAGEN_EXISTING_CASES = "overclaw.datagen.existing_cases"
DATAGEN_COVERAGE_GAP_COUNT = "overclaw.datagen.coverage_gap_count"

# ---------------------------------------------------------------------------
# Seed data validation / coverage (overclaw/optimize/data_analyzer.py)
# ---------------------------------------------------------------------------
SEED_VALIDATION_TOTAL_CASES = "overclaw.seed_validation.total_cases"
SEED_VALIDATION_VALID_COUNT = "overclaw.seed_validation.valid_count"
SEED_VALIDATION_INVALID_COUNT = "overclaw.seed_validation.invalid_count"
SEED_COVERAGE_QUALITY_SCORE = "overclaw.seed_coverage.quality_score"
SEED_COVERAGE_CASE_COUNT = "overclaw.seed_coverage.case_count"
SEED_COVERAGE_GAP_COUNT = "overclaw.seed_coverage.gap_count"
SEED_COVERAGE_UNCOVERED_RULE_COUNT = "overclaw.seed_coverage.uncovered_rule_count"
SEED_COVERAGE_SUGGESTED_ADDITIONAL_CASES = (
    "overclaw.seed_coverage.suggested_additional_cases"
)

# ---------------------------------------------------------------------------
# Coding agent (overclaw/coding_agent/{__init__,agent}.py)
# ---------------------------------------------------------------------------
CODING_AGENT_MODEL = "overclaw.coding_agent.model"
CODING_AGENT_INPUT_FILE_COUNT = "overclaw.coding_agent.input_file_count"
CODING_AGENT_MODIFIED_FILE_COUNT = "overclaw.coding_agent.modified_file_count"
CODING_AGENT_STEPS_TAKEN = "overclaw.coding_agent.steps_taken"
CODING_AGENT_MAX_STEPS = "overclaw.coding_agent.max_steps"
CODING_AGENT_TOKENS_IN = "overclaw.coding_agent.tokens_in"
CODING_AGENT_TOKENS_OUT = "overclaw.coding_agent.tokens_out"
CODING_AGENT_LOOP_STEPS = "overclaw.coding_agent.loop_steps"
CODING_AGENT_EXIT_REASON = "overclaw.coding_agent.exit_reason"

# ---------------------------------------------------------------------------
# LLM call metadata (overclaw/core/tracer.py + overclaw/utils/llm.py)
# ---------------------------------------------------------------------------
LLM_MODEL = "llm.model"
LLM_PROVIDER = "llm.provider"
LLM_MESSAGES_COUNT = "llm.messages_count"
LLM_TOOLS_PROVIDED = "llm.tools_provided"
LLM_TOOL_CALLS = "llm.tool_calls"
LLM_PROMPT_TOKENS = "llm.prompt_tokens"
LLM_COMPLETION_TOKENS = "llm.completion_tokens"
LLM_TOTAL_TOKENS = "llm.total_tokens"
LLM_COST = "llm.cost"
LLM_ERROR = "llm.error"
LLM_ELAPSED_SECONDS = "llm.elapsed_seconds"
LLM_REQUEST_MESSAGE_COUNT = "llm.request.message_count"
LLM_REQUEST_MESSAGE_CHARS = "llm.request.message_chars"
LLM_REQUEST_TOOL_COUNT = "llm.request.tool_count"
LLM_REQUEST_KWARGS = "llm.request.kwargs"
LLM_USAGE_PROMPT_TOKENS = "llm.usage.prompt_tokens"
LLM_USAGE_COMPLETION_TOKENS = "llm.usage.completion_tokens"
LLM_USAGE_TOTAL_TOKENS = "llm.usage.total_tokens"

# ---------------------------------------------------------------------------
# Tool call metadata (overclaw/core/tracer.py)
# ---------------------------------------------------------------------------
TOOL_NAME = "tool.name"
TOOL_ARG_KEYS = "tool.arg_keys"
TOOL_ERROR = "tool.error"
