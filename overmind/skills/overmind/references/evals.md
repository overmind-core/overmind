# Evaluations

An **evaluator** is a rubric (code check or LLM judge). An **eval set** is a
named grouping of evaluators, optionally mapped to a capability. An **eval run** scores a chosen
dataset cell with the **eval** contracts using those evaluators.

The dataset's intent **must** be eval and the chosen cell must fit. Call
`list_datasets`, pass its dataset UUID to `inspect_dataset`, and use
`query_dataset` to verify the chosen cell. Eval runs and optimizer experiments
refuse `train` and `pending` datasets — see
[SKILL.md](../SKILL.md#dataset-contracts--read-first-they-gate-every-workflow)
and [datasets.md](datasets.md) to land one. The run uses the version it
read, which freezes it.

Use `prepare-evaluation` for readiness and `evaluate-change` for a run versus
a baseline. Evaluation datasets must have `intent="eval"`.

## Prepare

Use `create_eval_set` with a name and selected `evaluator_ids` to group existing
library evaluators. Capability is optional; omit it or pass null for a reusable
project set. Members receive their applicable roles. Creation is atomic and
does not activate the set or launch an evaluation. Inspect the returned
`overmind://eval-sets/{eval_set}` resource for names, kinds, roles, and capability
mapping. Only capability-mapped sets can be activated for live trace scoring.

1. Call `check_evaluation_readiness` with the dataset UUID, chosen cell UUID,
   optional eval set, and `mode="existing"` or `mode="generate"`.
1. Read `ready`, evaluator applicability, variable binding status, eval-set
   state, and credit availability.
1. If an evaluator is missing or needs revision, collect the human rubric and
   call `upsert_evaluator`. It supports `llm_judge`, `trajectory`,
   `deterministic`, `statistical`, and `agentic` kinds, with the schema and
   scope values in its MCP input contract.

Do not guess evaluator ids or bindings. Use the evaluator and eval-set data
returned by readiness and keep sensitive values out of rubric/config JSON.

Configurable rubric judges default to generative. Jev is an explicit opt-in after
validating quality on representative labelled examples; confidence alone is not
a quality gate. Fixed grounding, classification and workshop decision services
use Jev with generative fallback. Authoring and holistic reasoning remain
generative. `upsert_evaluator` accepts `config.decision` with `backend` (`jev` or
`generative`), `model` (`typesafe/jev-1.13`), `min_confidence` (default `0.9`) and
`version` (`1`). Low confidence, insufficient evidence where a binary verdict is
required, oversized context and provider failures use the generative fallback.
Independent checks retain accepted answers and recheck only unresolved questions;
failed behaviour steps still receive a full causal review. Generated resolutions
are distinguished from Jev choices and confidence in the score provenance.
Snapshots preserve the policy; score `_decision` metadata records actual model,
answers, confidence and fallback reason. Confidence is not measured accuracy.
Use human-labelled examples to calibrate a policy before relying on its threshold.

## Run and compare

Generate mode evaluates a raw model against recorded context; it does not invoke
the live application or resolve an onboarding/document ID. Keep the full evidence,
task prompt and tool-result history in the eval input and the target separately.
Generate variants snapshot their prompt; row-level system turns take precedence.
Dataset sampling draws across the complete pinned version, balancing available
task labels, and repeats the same selection for the same version and budget.

Call `run_evaluation` only after readiness is green. Supply a run name, eval
dataset UUID, chosen cell UUID, optional eval set/evaluator ids, variants, and
bounded `max_items` or sampling as appropriate. It returns a run id, variants, and an
`overmind://eval-runs/{eval_run}` resource.

Poll an asynchronous run with `get_job(kind="eval_run", id=...)` and read its
resource. The run statuses are `pending`, `running`, `completed`, `failed`,
and `cancelled`.

The eval-run resource includes up to five sample inspections with grader reasoning
and separate `io.input`, `io.output`, `io.output_messages`, and `io.reference`.
`io.input_source=recorded` means the initial model-runner request was captured,
including its system prompt and tools; it is not every subsequent replay request
or provider-specific chat-template text. `dataset` is the verified pinned input
row, not proof of the exact request sent in a historical run. `unavailable` means
neither source can be read. Payloads are bounded for MCP; `samples_truncated`
indicates more samples exist. Do not infer an entire run's failure cause from one
sample or interpret grading-reference content as model input.

Call `compare_evaluations` with the current run and a baseline. Report the
authoritative `overall`, evaluator rows, and `trust` fields. Comparison status
is `improved`, `regressed`, `unchanged`, `added`, or `removed`.

Use `annotate_evaluation_sample` only for an explicit human label. It creates
or updates the supplied sample annotation; it is not a judge replacement.

Finetune and optimizer loops create their own incumbent / experiment
baselines — a manual eval run beforehand is only for eval-vs-eval
comparisons you drive yourself. See [finetuning.md](finetuning.md) and
[optimizer.md](optimizer.md).
