# Platform improvements: implementation and qualification

This change set covers the 32 findings in the Jev session retrospective and integrates `Wokshop-v3` at `e65b7ba99ae7abe034b8949da02bf3cdd74f3238`. It is isolated from the running deployment. The active experiment is `a356c75d-788a-4a35-bd30-1d24ae7afab8`; its saved runtime and completion protocol remain authoritative. No live migration, container restart, worker replacement, extra training run or model activation is part of this qualification.

## Finding-to-proof map

The table maps implemented behavior to repeatable fixtures. A fixture proves its stated case; it does not establish universal schema support, production network throughput or model quality. Run results and source hashes are in the qualification receipts described below.

| Finding                                    | Implemented change                                                                                                                 | Repeatable evidence                                                                                 |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 1. Implicit training contract              | Dataset-derived native objective, supported method and probability output shown before launch                                      | `test_decision_training.py`, `test_decision_loss.py`, `test_mcp_finetuning.py`                      |
| 2. Meaning-changing preparation            | Keep distributions, option order, weights, blank evidence and duplicate observations; semantic changes require a concrete proposal | `test_decision_workshop.py`, `test_dataset_approvals.py`, `test_training_transfer.py`               |
| 3. Preparation before exploration          | Source-bound plans with mappings, assumptions, families and checks; written intent and purpose continuations retain plan guards    | `test_workshop_planning.py`, `test_workshop_intent.py`, `test_dataset_agent.py`                     |
| 4. Unsupported quality claims              | Executed evidence and measured coverage; technical, preservation, coverage and semantic outcomes remain separate                   | `test_dataset_workflow_safety.py`, `test_workshop_semantic_checks.py`                               |
| 5. Incorrect split identity                | Input-only identity plus declared/nested groups and document/synthetic lineage; preserve selected duplicates                       | `test_dataset_split.py`, `test_finetuning_split.py`, `test_workshop_documents.py`                   |
| 6. Manual pilot creation                   | Representative-pilot request flow, deterministic stratified selection and parent coverage receipts                                 | `test_decision_workshop.py`, `pilot-request.test.tsx`                                               |
| 7. Ambiguous mutation targets              | Exact nonblank version/cell references; stale or ambiguous mutations reject before changes                                         | `test_dataset_workflow_safety.py`, `test_dataset_chain.py`, `test_dataset_agent.py`                 |
| 8. Different readiness/quote/launch inputs | Shared selection record and explicit train, separate validation and evaluation versions across Console/REST/MCP                    | `test_mcp_finetuning.py`, `test_training_forecast.py`, `use-train-wizard.test.tsx`                  |
| 9. Hidden evaluation boundaries            | Inspectable content/group overlap receipts and input-only suite sealing; no claim of paraphrase/pretraining disjointness           | `test_dataset_split.py`, `test_native_evaluation_plan.py`                                           |
| 10. Wrong native consumer                  | Native requests reject target leakage and chat consumers; dedicated probability scoring path                                       | `test_decision_prediction.py`, `test_decision_workshop.py`, `test_native_evaluation_plan.py`        |
| 11. Unbounded import                       | Batched landing, conversion and validation; append/replay stream disk files and retain late fields/errors                          | `test_dataset_streaming.py`, `test_dataset_scale.py`, `test_training_artifact_streaming.py`         |
| 12. Worker-loss loops                      | Durable operation ownership, redelivery guards and reconciliation; old callbacks cannot take over new work                         | `test_dataset_cancellation.py`, `test_dataset_dispatch.py`, `test_dataset_agent.py`                 |
| 13. Whole-frame SQL allocation             | SQL reads registered files with a bounded DuckDB budget; row-local transformations stream                                          | `test_dataset_store.py`, `test_dataset_streaming.py`, `test_dataset_scale.py`                       |
| 14. Statistics reliability                 | Isolated statistics subprocess, bounded concurrency, immutable cache and labelled approximate profiles                             | `test_dataset_statistics.py`, including an actual killed child and a successful subsequent request  |
| 15. Redundant transfer                     | Gzip initial upload; checksummed ordered selection keys reuse exact prepared token artifacts across chat/native runs               | `test_training_transfer.py`; `synthetic-network-transfer.json` for a real Modal Volume round trip   |
| 16. Restarted tokenization                 | Commit closed token shards with source offsets and identity; resume verified shards only                                           | `test_preparation_resume.py`, including actual SIGKILL and byte-identical resumed artifact/report   |
| 17. Repeated option tokenization           | Shared optimized boundary path and preparation counters                                                                            | `test_decision_training.py`; historical 8,330-decision / 84,072-check equivalence receipt           |
| 18. Partial cancellation                   | Local process reaping, provider acknowledgement and pending ownership; clean up created runs when receipt binding fails            | `test_dataset_cancellation.py`, `test_dataset_agent.py`                                             |
| 19. Opaque continuation                    | Attempt clocks, saved/restored steps and explicit verification state cross provider/API/UI boundaries                              | `test_training_telemetry.py`, `test_decision_resume.py`, `training-monitor.test.tsx`                |
| 20. Silent parameter changes               | Requested/effective receipts; explicit unsupported settings reject rather than clamp                                               | `test_training_submission.py`, `test_mcp_finetuning_receipts.py`, `test_finetuning_baseten_plan.py` |
| 21. Misleading forecast                    | Match objective, recipe, approximate length and recorded effective GPU/count; unmatched recipes stay unquoted                      | `test_training_forecast.py`, including changed/missing historical hardware                          |
| 22. GPU ledger shown as total cost         | Cost coverage, unpriced components, training-only ETA and absence of enforced cap remain explicit                                  | `training_record.py`, `test_training_forecast.py`, `training-monitor.test.tsx`                      |
| 23. Wrong/stale metrics                    | Objective-specific UI; newest bounded MCP history with truncation metadata; heartbeat and counters distinct from loss              | `test_mcp_finetuning.py`, `test_training_telemetry.py`, `training-monitor.test.tsx`                 |
| 24. Validation confused with quality       | Independent native suites and benchmark results; loss and reload verification do not assert quality                                | `test_native_evaluation_plan.py`, `test_decision_benchmark_scoring.py`                              |
| 25. Aggregate-only conclusions             | Paired coverage, raw/calibrated metrics, grouped intervals, source slices and retained incompatibilities                           | `test_decision_benchmark_scoring.py`, `test_native_evaluation_plan.py`                              |
| 26. Ambiguous preparing status             | Transfer, tokenization, loading, validation and training stages with units, heartbeat and unknown states                           | `test_training_telemetry.py`, `test_preparation_resume.py`, `training-monitor.test.tsx`             |
| 27. Hard-to-find experiment                | Run names, pinned dataset links, creation-time visibility and downloadable run record                                              | Console training list/monitor component fixtures and generated-client typecheck                     |
| 28. Invisible research plan                | Native calibration/final plan scheduled on the run, project-scoped polling, comparison results                                     | `test_native_evaluation_plan.py`, `test_mcp_finetuning.py`, Console component tests                 |
| 29. Chat-held orchestration                | Stable launch key, immutable selection, saved accepted findings, durable evaluation sequence and submission reconciliation         | `test_training_submission.py`, `test_mcp_finetuning.py`, `test_native_evaluation_plan.py`           |
| 30. Mutable runtime deployment             | Content-addressed worker apps and explicit environment; retries/evaluations pin releases; retention protects uncertain submissions | `test_training_release.py`, `test_modal_volume_retention.py`, `test_native_evaluation_plan.py`      |
| 31. Fragmented reproducibility             | Requested/effective run record and repeatable qualification runner; historical notes labelled as historical                        | `training_record.py`, `scripts/qualify_training_platform.py`, generated receipts with source hashes |
| 32. Expert-only diagnostics                | Latest stage, observer state, counters, provider ownership, heartbeat and checkpoint history across surfaces                       | `test_training_telemetry.py`, `test_mcp_finetuning.py`, `training-monitor.test.tsx`                 |

## Workshop branch integration

The branch brings the written-request entry, persistent intent choice, exploration-only workflow, document/image extraction, attachment import, proposal retirement, unified Notebook chat, ChatGPT funding/preferences and local Codex SDK initialization. Integration preserves the main branch's account-scoped MCP/OAuth, analytics and newer dependency versions. SDK version is 0.1.83. Branch migrations were reconciled into `0018_workshop_sources_and_funding` after the existing 0013–0017 chain; conflicting historical migration numbers were not copied.

Reproduced integration regressions and their repairs:

- Pending purpose blocked inspection: read-only exploration now works while purpose-dependent changes wait.
- Attachments read the entire existing table, and replay returned a frame where the runner expected a file: both now stream verified files.
- Written intent bypassed plan/audit-budget guards: preparation state is separate from generation authority and survives intent/proposal continuation.
- Delayed provider/tool callbacks could change a newer operation: task ownership is checked before mutation; receipt binding failure cancels the created provider run.
- Cleanup used the old app name and treated an unacknowledged run as an orphan: it uses the explicit release and preserves saved submission identities.
- Forecasts reinterpreted old hardware using today's planner: matching now requires the recorded effective GPU and count.
- The new MCP tool exceeded the 40 KiB manifest limit: repeated prose was shortened, preserving the limit and strict schemas.

The branch's OCR fixtures use actual local Tesseract for native/scanned/mixed PDFs and PNG/JPEG/WebP. Original bytes, document/page identity and evidence provenance remain available. Extraction does not manufacture training answers. ChatGPT tests use protocol fixtures; no live account connection or paid ChatGPT turn was made for this integration.

## Reproduction

Use Python 3.13, Bun, the root dev/test dependencies, the SDK tracing/dev dependencies, and Tesseract with English/orientation language data. CPU torch is required for numerical decision/checkpoint tests; the qualification environment supplies it separately without modifying the live trainer stack.

```sh
uv sync --group dev --group test
uv sync --project overmind --extra tracing --group dev
PYTHONPATH=/path/to/cpu-torch:. .venv/bin/python scripts/qualify_training_platform.py --output /absolute/qualification-directory
```

The runner uses `tests.settings`, an isolated in-memory SQLite database and local provider fixtures. It records each command, environment, duration, exit status, log, working-file hashes and whether files changed during the run. It checks backend tests/lint/format, Console tests/lint/types/design/build, SDK tests/lint/format, schema consistency and actual fresh migration execution. Pre-commit runs separately over all changed files. The SDK command follows repository practice by excluding `test_spans.py`.

Session receipts are under the chat's `jev-training-setup/platform-improvements/`: initial qualification failures remain preserved; `workshop-integration/qualification` records the integration run and `final-qualification` records the final frozen source run. `workshop-integration/before-integration-files.tar.gz` preserves the previous isolated checkout. The release report lists follow-up results and limits rather than rewriting failures as passes.

## Performance evidence and limits

The real network fixture uses 8,320 synthetic rows: raw JSONL 9,633,220 bytes, gzip 4,172,762 bytes, selection keys 266,240 bytes. Upload observations were 7.809 s, 5.316 s and 1.549 s respectively; download observations were 5.679 s, 3.602 s and 1.428 s. Compression took 0.049 s. Every downloaded artifact passed byte/decompression checks. This is one observation per artifact on the existing Modal Volume service, with uncontrolled cache/order/network overhead; it is not a measured full-run speedup. The fixture's files were deleted after verification.

The earlier boundary qualification measured 90.94 s versus 4.75 s with identical digests. That roughly 19.1× result applies to that tokenizer boundary fixture, not end-to-end training.

Bounded streaming avoids retaining full row payloads. Split metadata can still scale with row count, and ordinary Python scripts keep whole-frame semantics. The original API SIGSEGV cause is unproven; isolating statistics limits its failure impact without claiming to have identified that cause.

## Release boundaries

- This checkout has not replaced the running API, Celery workers, database or Modal trainer. The current unpinned experiment must complete under its existing protocol before adopting this API/runtime scheme.
- Fresh SQLite migration execution and service workflow fixtures do not prove production PostgreSQL locking, live broker redelivery or a newly deployed GPU image. Those require release-environment qualification.
- The browser tool rejected the isolated file preview. Component tests, typecheck, production build and design checks are recorded; fresh browser visual/end-to-end approval is not claimed. Images imported from the branch remain historical branch evidence.
- Automatic approval review rejected a proposed real-data external transfer. That action did not run. Network qualification uses synthetic data; no benchmark references or current training data were exported for this test.
- Final quality, realized all-in billing and the current full run's first 24-hour continuation remain outstanding. Preserve the pilots' MedQA/doc-NLI regressions; sample imbalance has not been established as their cause. No new evaluation plan is attached to the current job by this change set.
