# Typed decision preparation and representative sampling

The quality pilot exposed two preparation failures: evaluation intent selected a chat playbook that removed valid blank states and normalized publisher probabilities, and stratification required the agent to reconstruct a large quota table from truncated tool output. The two affected preparation turns were cancelled through their provider, then interrupted through Celery's soft-limit path because the local stream did not settle. Both datasets returned to idle. Their source versions remain intact. No quality-pilot or full-corpus training job was launched.

## Scope and acceptance evidence

Adapt the shared preparation function and consumer profiles to native decision train/eval contracts. Preserve supplied data; invalid native rows stay visible as technical failures. Add a trusted, file-backed sampling primitive exposed through the existing dataset chat and saved as a reproducible cell. Sampling remains a reviewed selection, not synthetic generation or an implicit train/eval split. Existing MCP messaging/inspection/run tools cover the same intent; no new public MCP mutation or cancellation API is needed.

Failure cases to exercise before implementation:

- A self-contained question with empty evidence is discarded; duplicate observations disappear; option order, weights or soft probabilities change.
- Binary scalar supervision is mapped to the wrong order, invalid targets are normalized, or a malformed row late in the source is missed.
- Evaluation requests include targets or native probability references become chat answers. Mixed native/chat rows lose their task identity.
- Automatic preparation pays for generic semantic judges or calls a normalized distribution proof of label truth.
- Sampling reads the full source into memory, loses a rare stratum, allocates more than a stratum contains, silently underfills, depends on batch boundaries, or changes selected rows/provenance.
- A sample bypasses review, reuses stale preview data, changes after approval, or cannot rerun from its stored recipe.
- Truncated diagnostic stdout is presented as a complete result.
- JSON-encoded native inputs evade probability-reference validation, or a native evaluation dataset reaches ordinary chat generation through REST or MCP.

Verification uses the actual landing → workshop tool → isolated runner → reviewed cell → contract/export path, with a corpus spanning multiple storage batches and a late malformed native row. No paid judge is needed for deterministic coverage. Record commands, fixtures, environment and observed results below when run. Live qualification will repeat the approved 50,000-row selection against the frozen 1,164,217-row native corpus after the local path passes.

## Run record

Verified on 2026-10-02 in the local Docker deployment, project `1e3f3e92-b50d-4590-85ed-97921d132d3c` (`overmind-4`). The tests use the repository uv environment and pytest database settings; no live model or judge is called by the tests. The live Workshop uses the configured Cursor engine, existing Postgres/Redis/Celery services and project CLI credentials. Secrets are never embedded in the commands or receipts.

### Repeatable local checks

```bash
uv run pytest tests/test_decision_workshop.py tests/test_dataset_preparation.py tests/test_dataset_streaming.py tests/test_dataset_context.py tests/test_dataset_consumer_contract.py tests/test_dataset_agent.py tests/test_dataset_contract.py tests/test_workshop_semantic_checks.py -q
uv run pytest tests/test_decision_workshop.py tests/test_dataset_preparation.py tests/test_dataset_agent.py tests/test_dataset_context.py tests/test_mcp_datasets.py -q
uv run pytest tests/test_decision_workshop.py tests/test_mcp_evaluations.py tests/test_eval_api.py tests/test_dataset_consumer_contract.py -q
make generate_api_client
```

The first regression run recorded 175 passes and two old expectations failing: the cached preview fixture still used the whole-frame preparation script, and the internal agent tool list omitted `sample_rows`. Both fixtures were updated. The follow-up recorded 143 passes. Two additional workflow cases first reproduced JSON-encoded native references escaping validation and native rows being marked chat-compatible. After fixing both, the final consumer/API run recorded **168 passes** (41.36 seconds). Its 234 warnings concern the existing test JWT key length. These suites overlap; their counts must not be summed. No full repository or frontend suite was run. Client regeneration completed with no generated-file diff.

Logs: `/tmp/decision-workshop-regression.log`, `/tmp/decision-workshop-regression-rerun.log`, `/tmp/decision-workshop-consumer-before.log`, `/tmp/decision-workshop-consumer-final.log`, `/tmp/decision-workshop-api-client.log`, `/tmp/decision-workshop-precommit.log`. Targeted pre-commit hooks passed, including Ruff, Markdown formatting, whitespace and secret checks. `git diff --check` passed.

The new fixtures land complete datasets and exercise the actual Workshop tool, isolated runner, reviewed preview, acceptance, replay and use/export path. They include a 20,003-row source, a 503-row sample retaining a singleton stratum, duplicate blank-state decisions, weighted soft targets and a malformed reference beyond the first storage batch. The REST/MCP workflow verifies that native probability data is rejected before ordinary chat generation creates a run.

### Live corpus qualification

Training dataset `34aa7d49-bf40-4488-ad53-7453ba1b0bca` retains its full 1,164,217-row parent `6655c200-662a-4cc7-934e-50e6654842d2`. The Workshop produced and reviewed a single saved recipe:

```python
df = sample_rows(
    rows=50000,
    seed=73491,
    stratify_by=["source", "decision.kind"],
    target_type=True,
    minimum_per_stratum=1,
)
```

Approved cell `47f47acd-64f4-426d-aeb9-62f7c5bca331` is version 1.2 with 50,000 rows and fingerprint `354bf3eb76822635754771dd555432530fe56a5920ca80ca7e5b14055a2d4042`. Independent exported-row verification compared every selected decision and its metadata with the immutable 2.5-million-row publisher source and independently calculated quotas. It found:

- 342 sources, all 911 strata, 49,148 groups; 37,071 choice, 9,113 binary and 3,816 score rows.
- 5,516 soft-target and 44,484 hard-target rows; every probability and model field preserved.
- 50,000 ordered, unique original source-row identities. Source/group lineage remains present. Existing content lineage can gain the current native-input hash; it is additive rather than byte-identical to historical provenance.
- Export SHA256 `91517aa99a23aa7cab6200395b6b662a19d534437f681da23a73acf3e12d7c25`.

Evaluation dataset `3b57208f-be5c-439e-9746-9b9cf4d3fe7e` was repaired through existing Workshop cell edits. Native cell `8008072a-5720-4b4f-a747-a440059d7db6`, version 1.2, has all **15,000** original rows from 651 sources, including 149 valid blank states and 1,414 soft targets. All original fields, native decision values, references and target-free requests match the immutable publisher validation source. Export SHA256 is `71d10b7dc506f0215a67264679c0f47e499cfcef7013880d2caece1f7bef45fa`.

Evidence and repeatable scripts are in `/Users/tyleredwards/.codex/visualizations/2026/10/02/01a0fd8c-a569-7392-bee9-f6ce50b00194/jev-training-setup/quality-pilot/`: `verify_sample_export.py`, `sample-verification.json`, `verify_native_eval_export.py` and `native-eval-verification.json`. Export via sibling `benchmarks/export_for_verification.py DATASET CELL LABEL`, then run each verification script with `uv run python`. The source Parquet files, export receipts and selected source identities are retained in that experiment directory.

The live agent initially mislabeled structural checks as semantic passes. Its report was corrected through `record_quality_review`: all 50,000 rows pass output schema, while answer support, input sufficiency and task alignment each have 50,000 unknowns. Prompt/tool guidance now explicitly distinguishes probability validity, licensing and label truth. This remains an agent-authored audit, not independent proof of semantic correctness; no paid semantic judge was run during qualification.

The approved overlap-audited development panel is active as cell `75b4d1c1-ca71-4c0b-ada3-01db05b0cd05`, version 1.3: **8,114 rows across 337 sources**, comprising 6,957 familiar-family and 1,157 unseen-family rows. `seal_development_suite.py` verified every selected row against the independent mask, every source value/reference against raw validation data, and all excluded-group constraints before writing source, panel and pooled views. Their identical target-free inputs have SHA256 `c13689ec504ba3a6492f76d28728687423d30ae6bb9ac1dc095475aa8b93ae54`; references are stored separately. MCP training readiness reports zero train/development overlap on the selected versions. This complements the prior input-only exact/substring group audit; neither check proves absence of paraphrase or pretraining contamination. Reserved final/calibration predictions remain untouched.

### Boundaries and remaining work

This change is in preparation, profiling, streaming sampling, proposal review and consumer compatibility. It does not change the already-qualified model architecture or decision cross-entropy loss. Native benchmark execution still uses the dedicated decision worker, not ordinary chat EvalRun generation; REST and MCP now explain that incompatibility. Full native evaluation integration, calibrated native cost estimates and a public dataset-agent cancellation operation remain separate platform work.

Exact preparation `41a4f210-d1a5-43d5-a0cb-86b15d410768` is ready: 57,210 train-plus-validation rows, 22,890,702 tokens, maximum 30,086 tokens and zero incompatible rows. Its artifact SHA256 is `869ef58559568655b11a5f8c0576a043d1a9f5aa112721e43ed1c59c8b30ed95`. The approved 50,000-row, one-epoch pilot launched at 22:51 UTC as job `f0e8fa63-aca4-4d8d-bacf-c66dbc3a680b` (**Jev native 50k general-decision quality pilot**), with separate 7,210-row validation, seed 73491 and all ordinary chat evaluation flags disabled. Starting the job freezes the selected versions; cell IDs and fingerprints remain the stable identities. Full training remains held.

The generic cost tool returned $1.7058 / 26 minutes on H100, which is not a calibrated estimate for the qualified H200 native recipe. Retain the conservative pilot-plus-development-evaluation envelope of $15–$40 / 2–5 hours until measured. No model-quality claim follows from these data checks. The launch request and receipt are saved in the experiment's `quality-pilot` directory.
