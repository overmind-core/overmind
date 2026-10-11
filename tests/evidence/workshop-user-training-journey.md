# User-style Workshop to training acceptance

Authorization: $100 maximum across this continuation's paid preparation, training
and evaluation. Previously recorded costs from earlier audits are not treated as
zero; no earlier unresolved operation is resubmitted. No new paid call has been
made at the start of this record. Use small single-model bounded work with large
headroom; incomplete provider accounting is not an all-in invoice or hard cap.

User-style scenarios, executed by the current native coding agent (not independent
blinded agents):

1. "Prepare these matching examples so I can train a small model. Keep a review
   trail for questionable examples, avoid leaking identities between training and
   validation, and show me the complete upload-to-training workflow within the
   approved test budget."
1. "Turn these security documents and screenshots into reusable source evidence.
   Keep track of where everything came from and flag anything that needs review."
1. "Use the same preparation on another compatible file, adapt it where necessary,
   and make sure I can repeat it without losing or duplicating observations."
1. "Combine these files, recover an interrupted upload, and help me inspect a large
   dataset without a stalled request or an unreadable response."

For scenario 1, fresh upload `369b56c9-3f77-48a6-8c5c-5aa7f217b602` preserves
`sample_10000.json`, SHA-256
`fe2f701f6034d3adaa586ef747d3c3855ca10fcbac1de97fa3dd7e487058c521`.
The source has 10,000 rows under `pairs`; explicit `--json-rows-field pairs` was
used. The first sandbox command returned actionable `network_permission_denied`;
the identical command succeeded with the host's scoped permission. No browser.

Expected preparation invariants, specified before execution: all 10,000 supplied
judgements survive the full preparation; original nested records remain retained;
model evidence excludes top-level identity/reference links and label-like target
flags; connected entities stay in one group; repeat exports match; all output
parents remain traceable; bounded sampling preserves complete selected groups;
train/development/final groups never overlap. This validates preparation mechanics,
not label truth or KYC suitability. The native agent authors the retained scripts.

## Results, 2026-10-09 local / 2026-10-10 UTC

The coding agent completed upload → retained four-step package → preview →
publication → entity-disjoint partition → exact token preparation → real Modal
LoRA training → held-out native comparison. It did not merely create local files.
This is one qualified small-model training path, not qualification of every model,
training objective, provider or production workload. All paid actions used MCP;
file bytes used the installed CLI. Local code/log inspection was used to fix proven
platform faults. No browser, activation or live routing changes were made.

| Evidence                     | Observed outcome                                                                                                                                                 |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Fresh ingestion matrix       | 38/38 expected outcomes; JSON/JSONL/gzip, CSV/TSV, Parquet, text/Markdown, images, PDFs, precision, nulls, Unicode, malformed/oversized inputs                   |
| Large datasets               | 10,000 and 100,000 rows; 100,000-row landing/verification case took 9.377 seconds                                                                                |
| Real-data repeated execution | 9 document/tabular/chat preview/publication runs; six more handbook reuse/derived-branch runs                                                                    |
| Complex real handbook        | Two fresh 20,604,056-byte uploads, identical 1,381-row fingerprints; 12.399 and 11.181 seconds                                                                   |
| Document size coverage       | Native PDFs through 2,000 pages; 2,001 rejected; scanned and mixed-page cases; images and byte-limit boundaries                                                  |
| Read performance             | Median inspect 26.2 ms, p95 43.2 ms; query median 31.6 ms, p95 46.9 ms, in this local matrix                                                                     |
| Query recovery               | Deliberately expensive query rejected at 10.081 seconds; five concurrent inspections took 48–93 ms; subsequent count still returned 10,000                       |
| Full 10,000-pair recipe      | Four meaningful scripts; all 10,000 observations retained through target projection; explicit group sample produced 404 rows; 40.386 seconds publication         |
| Frozen partitions            | 324 train / 60 development / 20 final; 384 entity groups; independent exported-row verifier found zero cross-role identity overlap                               |
| Exact preparation            | 384 rows, 316,478 tokens, longest 6,196 tokens, zero incompatible rows; same token artifact reused                                                               |
| Corrected native training    | One epoch, 21 optimizer steps, effective batch 16; saved checkpoint reloaded on 64 decisions with zero observed probability difference                           |
| Held-out comparison          | 20/20 predictions per model; zero missing, invalid or incompatible results; no exact train/final overlap                                                         |
| Reuse and recovery           | Manual saved binding rebuilt 404 rows; run limit prevented another execution; restore/reselect worked; cancelled queued publication left active output unchanged |

## Product failures found and fixed

1. Short transformations paid coarse polling delays. Controller and container
   polling were reduced; receipts now expose measured phases and aggregate preview
   time. Timeouts no longer claim a completed script merely because cleanup ran.
1. An expensive query had no execution deadline. It now interrupts at the configured
   limit, bounds materialization and leaves source data and subsequent reads intact.
1. A manifest declared parameter values instead of types. That was an agent error;
   the platform's generic 400 hid the actionable correction. The error now names
   parameter type declarations, without leaking arbitrary server responses.
1. Native-decision readiness returned chat rankings and incompatible candidates.
   It now returns the native eligible catalogue without inventing rankings or prices.
1. The agent submitted `gradient_accumulation_steps=8`; the platform accepted but
   ignored it. That first job really ran batch 2. Unsupported accumulation and
   microbatch overrides are now rejected in estimates and launch before dispatch.
   The corrected job used the documented effective `batch_size=16`.
1. A worker reload abandoned the collector's five-minute Redis lock, causing minutes
   of unexplained stale checkpoint progress. A renewable 30-second lease now limits
   orphan-lock recovery while preserving a live collector. A disposable real Redis
   process-loss test verifies renewal and recovery; no GPU job was resubmitted.
1. Job success did not close its operational timeline. Finalization now records the
   refreshed terminal state; reconciliation repairs missing terminal events from
   saved completion facts. Live old receipts recovered without a provider call or
   fabricated heartbeat. Success/failure/cancellation and missed-write cases pass.
1. GPU charges and forecast duration used local completion-observation time.
   The delayed job recorded about 448 seconds against about 127 seconds of worker
   time. New charges and duration evidence use deduplicated worker receipts and
   measured hardware. Charge calculation, usage IDs and fetched rates are retained;
   missing/conflicting usage stays unmeasured. Historical entries were not rewritten.
1. The estimate response reported the default pre-training baseline even when the
   agent explicitly disabled it. The response now reflects the requested recipe.
1. Agents could not export retained original document bytes with the installed CLI.
   `dataset export --source SHA256` now verifies the download checksum and refuses
   overwrites. The real handbook's original bytes were successfully verified.

## Quality is not execution success

The unchanged foundation scored 15% accuracy and the corrected one-epoch model
25% on 20 held-out decisions. Predicting the majority label would score 80%
(16 positive, four negative). This is a poor model, not a successful quality result;
the sample is also much too small for a reliable production-quality conclusion.
Cross entropy improved from 1.3117 to 1.2368 but remains weak. No deployment was
made. The bounded run was explicitly marked non-quality qualification.

The native agent's recipe preserved labels but left nested relationship identifiers
inside domain properties and used opaque positive/negative options. These are
agent-owned design/semantic limitations, not facts Workshop should silently repair.
The platform preserved the source, transformation code, targets and split lineage.
No claim of label truth, complete identity-leakage removal or KYC approval suitability
is made. PDF extraction checks establish mechanics and retained provenance, not
complete reading order, table understanding or semantic correctness.

## Exact receipts

- Project: `e18b29b5-915d-45a7-80cd-77ffe6559205` (`financial-services`).
- Source dataset: `369b56c9-3f77-48a6-8c5c-5aa7f217b602`.
- Source cell: `1e6be2d8-1c31-48e3-b30e-c7ca400710dc`.
- Package: `45e518cd-83c4-4827-8498-4531a0fedb46`.
- Revision: `4b3ad974-0c1c-4868-a962-753811d4bc81`.
- Preview: `3ab58987-e1e9-4f99-8367-b58ff5a3543d`.
- Publication: `6104928b-376b-4b29-9c59-ec04fb38ae8b`.
- Full 10,000-row decision cell: `89bbe6eb-f7d3-40ed-82a4-9a761d8e2ad2`.
- Explicit 404-row sample: `8de529f5-9d5b-45eb-96ef-5186462549f5`.
- Partition: `925cda60-3d7f-4d0a-ad25-a3fa1bae4352`.
- Preparation: `b52ed6b0-9fe1-4f22-85a2-b774ab6fa7dd`.
- Initial wrong-batch job: `1df88ade-4951-45fd-abe9-90f1dfd7ba5c`.
- Corrected one-epoch job: `a2dc4fed-9cfe-4353-9f23-4703e275406e`.
- Native comparison: `5a8d85ab-9f63-4562-bec6-1532ad8b1a62`.
- Binding: `33c354c0-c9b8-4b50-86f7-bc3c26ec0321`, disabled, one authorized run.
- Cancelled publication: `c8d0b478-6040-4799-960e-153c492c4a14`, no output cell.
- Final billing smoke job: `bb803a90-2e4e-44e7-81c5-02a9cdfbd077`; final outcome below.

### Final paid smoke and budget

The final one-step smoke succeeded. Its checkpoint reload verified 64 decisions
with zero observed difference. The operational timeline says succeeded, 1/1 steps,
and retains the actual last heartbeat. The saved GPU charge, $0.1389, equals
126.620291169 worker-seconds × $3.95/hour; local completion was observed after 148
seconds and did not enter the charge. Initial baseline was explicitly off and the
effective worker setting confirms it.

This continuation's three training jobs recorded $0.7915 total GPU charges. Add
$0.010385 estimated training CPU/memory, $0.003453 shared preparation counted once,
and $0.122983 native comparison compute: $0.928321 recorded-plus-estimated total.
This deliberately includes the first two unchanged historical charges with the
incorrect observer-time basis; it is not an all-in provider invoice. The two worker
receipts imply approximately $0.1409 and $0.1398 GPU coverage at the observed rate,
versus their recorded $0.1609 and $0.4917. No ledger adjustment was applied.
Earlier audit costs remain separate and were not assumed zero. No paid work from
this continuation remains active; no new monitor or scheduled source binding was
enabled. The $100 budget was not approached.

### Final regression evidence

Suites overlap; these counts must not be summed as unique coverage:

- 62 Workshop/store/package regressions, with real isolated-container cases enabled.
- 44 SDK export/transfer/resume checks.
- 151 MCP catalogue/resources/prompts/document/transfer/training checks.
- 51 operational/data-first/MCP workflow checks.
- 71 shared lock consumer regressions.
- 44 collector/timeline/topology checks passed; one older default-memory-broker
  Redis test skipped. The explicit real-Redis process-loss case ran and passed.
  Python reports a fork-from-multithreaded-test-process deprecation warning.
- Latest cross-surface billing/forecast/training/MCP pass: 162 passed.

Logs are in `/private/tmp/workshop-gap-closure-regression.log`,
`source-export-after.log`, `workshop-cross-surface-regression.log`,
`workshop-workflow-regression.log`, `lease-cross-vertical-regression.log`,
`training-recovery-final.log` and `workshop-training-final-regression.log`.
Terminal/billing tests first reproduced failures, then were rerun after fixes.
Test-harness corrections (fixture identities, rate fixtures and provider step
fields) are not counted as product fixes. A spawn-based test attempt failed during
Django bootstrap; the final disposable-process test used the working fork harness.

```sh
WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea uv run --no-sync pytest tests/test_reusable_workshop_journey.py tests/test_workshop_mcp_acceptance.py tests/test_workshop_package_transfer_journey.py tests/test_dataset_store.py -q
WORKSHOP_REDIS_URL=redis://127.0.0.1:6379/0 uv run --no-sync pytest tests/test_task_lock.py tests/test_operational_progress.py tests/test_finetuning_observe.py tests/test_finetuning_reconciler.py tests/test_celery_topology.py -q
uv run --no-sync pytest tests/test_llm_and_modal_billing.py tests/test_workflow_measurements.py tests/test_training_forecast.py tests/test_finetuning_observe.py tests/test_operational_progress.py tests/test_mcp_finetuning.py tests/test_training_submission.py tests/test_training_experiment_workflow.py tests/test_mcp_resources.py tests/test_mcp_manifest.py -q
```

Installed CLI connection verification from `/private/tmp`, with no injected key or
API URL, returned MCP ready, upload/export ready, contract 5.7 and catalogue hash
`0b44c2eea2e02c419c96817a09b97393d2a5290f188f46e523c0dcecede65b01`.

Final verification: the independent export verifier passed again; replaying the
final training request returned the same succeeded job; the live estimate preserved
the disabled baseline. Scoped pre-commit, SDK Ruff checks/format, and `git diff --check` passed. Frontend checks were not run because this continuation changed no
UI. Exact retained package bytes were not reformatted after upload. Changes remain
uncommitted on the current branch; no deployment of GPU worker code was needed.

## Repeating the evidence

Requirements: existing local Compose API/Redis/workers and isolated Workshop runner;
saved account connection; installed editable CLI; source files above; Python/uv test
environment. Runtime image used:
`sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea`.
The full live matrix reuses fixtures in `/private/tmp/workshop-granular.mdoZkg`;
if absent, create them with `workshop_pdf_fixtures.py` following the PDF skill, then
pass their new directory. A repeated run key recovers saved work; choose a new run
prefix for genuinely fresh transfers. Never change a recipe under an existing key.

```sh
overmind connection check --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
uv run --no-sync python tests/evidence/workshop_granular_probe.py --directory /private/tmp/workshop-granular.mdoZkg --report tests/evidence/workshop-user-wide-results.json --run user-wide-20261009 --cli /Users/tyleredwards/.local/bin/overmind
uv run --no-sync python tests/evidence/workshop_platform_replay.py --run user-platform-20261009 --report tests/evidence/workshop-user-platform-results.json --cli /Users/tyleredwards/.local/bin/overmind --repeats 2
uv run --no-sync python tests/evidence/workshop_handbook_replay.py --dataset e459b19b-b26f-493b-828b-b6cb3cc385d7 --run user-documents-20261009 --report tests/evidence/workshop-user-documents-results.json --pdf /Users/tyleredwards/Downloads/Lakera_Handbook_AI_Security_for_Product_Teams-.pdf
uv run --no-sync python tests/evidence/verify_pair_training.py --source /Users/tyleredwards/Downloads/sample_10000.json --directory /private/tmp
```

The verifier requires CLI-exported `workshop-pairs-training-sample.jsonl`,
`workshop-pairs-train.jsonl`, `workshop-pairs-development.jsonl` and
`workshop-pairs-final.jsonl`. Their exact dataset/cell IDs are retained in the
JSON receipts beside this report. Package entrypoints are in `workshop_pair_training/`;
the server retains the exact uploaded checksummed package independently.

MCP requests, frozen selections, effective settings and final measurements are
retained in `workshop-user-training-receipts.json` and
`workshop-user-training-terminal-receipts.json`. `get_job` and returned resources
inspect the IDs above without resubmitting paid work. Preparation/training are not
automatically repeated by the no-cost fixture commands.

## Remaining product gaps and coverage limits

- Atomic resumable multi-file upload is unsupported; serial uploads expose
  intermediate versions. No claim that batch ingestion is solved.
- Native PDF recovery only handles entirely missed pages; partial omissions inside
  otherwise-extracted pages can remain. No layout/semantic completeness guarantee.
- Structured landing still lacks detailed storage/profiling stage measurements.
- Full invoice/budget enforcement is absent. Known receipt coverage is not an
  all-in provider bill or a hard monetary cap.
- Only the identified unsupported batch overrides were hardened, not every possible
  hyperparameter key. Broad schema validation remains a capability improvement.
- The earlier isolated 6.6-second inspection outlier was not reproduced. Fast fresh
  and concurrent reads do not establish its historical root cause.
- Native comparison `get_job.completed_at` remains null; the completed scoring
  stage has its observation timestamp. This metadata gap was found but not changed.
- No browser/UI checks, sustained multi-user stress, full 10,000-row GPU training,
  large-model/multi-GPU qualification or independent blinded-agent studies in this pass.

The 38 expected outcomes and repeated outputs pass; this is not a claim of zero
friction across the whole product. The initially ignored setting, stale progress
and inflated charge were real failures, not erased by their later regression passes.
