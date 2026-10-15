# Main integration readiness — 2026-10-14 (Pacific)

Status: application validation passed; production cutover prerequisites remain.
No production database, infrastructure or routing was changed. The deployment
procedure is in `scripts/workshop-rollout.md`. Companion reviews:
[documentation #68](https://github.com/overmind-core/docs/pull/68) and
[infrastructure #11](https://github.com/overmind-core/cloud-platform/pull/11).

## Scope and decisions

Branch `codex/rl-continuous-optimisation` at `0c5d35d` is merging main
`ff093cc0f50ded2f4ce96030e8908254f9ecaa43`; their merge base is
`32248f4a576eb76878065778bd8d3a482f6a2bf5`. The review covers 28 branch commits
and eight main commits, including the earlier evaluation branch merge.

| Area             | Integration decision                                                                                                                                                                                                     |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Console          | Preserve branch layouts, navigation, Workshop canvas, training setup, capability trajectories and evaluation visibility. The only functional frontend change in this integration is clearer upload-size validation copy. |
| Workshop         | Retain immutable packages, revisions, branch/fan-in execution, retained sources, lineage and durable publication receipts. Do not retain a second mutable Execution runtime alongside it.                                |
| Native decisions | Retain the existing FinetuningJob lifecycle. The user explicitly approved retiring main's separate DecisionJob runtime while preserving its records, costs, results and checkpoint references in DatasetHistory.         |
| Evaluation       | Retain branch span/trajectory scoring and frozen native comparisons. Main's trace-selection tests exposed two attribution gaps; both are fixed.                                                                          |
| Main bug fixes   | Retain context-limit error recognition, exact Console selector matching, checkpoint redirect protection and current ECS task-family revisions. Existing branch rsLoRA, warmup and checkpoint fixes remain.               |
| Cleanup          | Remove unused streaming LLM-tool code, obsolete capacity/incident scripts and an unreferenced table helper. Keep billing helpers still used by decision providers.                                                       |
| Documents        | Preserve branch OCR/native-text recovery, source export and extraction evidence inside main's isolated parser boundary. The landing worker streams bytes/progress and has no in-process fallback.                        |
| Attachments      | Retire mutable Execution APIs. Preserve attachment/parent/analytics/impact metadata, execution receipts and source bytes in the migration archive. New transformations use retained immutable packages.                  |
| Worker releases  | Add decision-serving and decision-evaluation deployment steps and missing worker identity inputs to the Modal workflow. Development releases were deployed for the MCP checks.                                           |

## Automated checks

- Final backend suite: **3,192 passed, 10 skipped**. The initial trace-attribution
  and superseded-schema failures were fixed before this clean full run.

- Final frontend suite: **875 passed** across 121 files. Typecheck, Biome and all
  design/contrast/control checks passed.

- SDK: **600 passed**. Version 0.1.92 remains above main's 0.1.90.

- Additional cutover/deployment tests: **14 passed**, including refusal to migrate
  with old consumers active and explicit restoration of recorded service capacity.

- Isolated parser tests cover DOCX, Markdown, text, native/scanned PDF, images,
  protected/empty/invalid documents, protocol/auth rejection and streamed OCR progress.

- Disposable PostgreSQL upgrades passed from current main, branch `0c5d35d` and
  an empty installation. Populated upgrades retained cell IDs, active selections,
  metadata, execution/decision/request records, checkpoint references and file bytes.
  Final physical columns match current models; a second migration plan is empty.
  `make check-migrations` passes with no model drift. Active legacy execution and
  decision jobs were separately proven to block cutover without losing records or files.

- Companion Terraform passed formatting, initialization without a backend and
  validation. No Terraform plan/apply or production migration was performed.

- Final pre-commit checks, backend lint and formatting passed. Companion docs
  passed Mintlify strict build validation and the broken-link check.

Logs are retained locally in `/tmp/overmind-prod-readiness/`.

## Live local MCP checks

Endpoint: `http://localhost:8000/api/mcp/`. Project:
`1e3f3e92-b50d-4590-85ed-97921d132d3c`. Platform operations used MCP; the installed
CLI transferred local file bytes. No Console/browser testing was performed.

| Check                        | Evidence                                                                                                                                                                                                                                                                                                                                                                       |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Draft then source upload     | Dataset `c80baf6f-3342-4ff4-9c83-6ab2fd27d433`; 32 synthetic observations with Unicode, nulls, one duplicate and integers above JavaScript's exact range. SQL string projection verified the retained integer values.                                                                                                                                                          |
| Immutable branching pipeline | Revision `2a08e7d6-0b21-40c3-8502-b0f05cf9406a`; billing/account branches each retained 16 rows and disjoint fan-in published 32.                                                                                                                                                                                                                                              |
| Preview                      | Run `a7663be4-036f-4265-8981-cdf2a275019f` completed without publication; absolute row-count checks remained deferred.                                                                                                                                                                                                                                                         |
| Publication                  | Run `28e94bd3-09bd-4e97-92a9-20054a71682e`; output `e5b91616-7edd-42f0-9145-6180c403153f`. All source identities, duplicate observations and nulls retained.                                                                                                                                                                                                                   |
| Retry/conflict               | Identical publication key returned the same run; changed parameters under that key were rejected.                                                                                                                                                                                                                                                                              |
| Failure/cancellation         | Deliberate failed run `abe309d8-c61b-463e-a61a-4547ede8d771` and cancelled run `5576a413-f985-44de-bc31-38aa173dfe2e` published no output; active output remained unchanged.                                                                                                                                                                                                   |
| Grouped holdouts             | Plan `40cc7565-65b7-4c97-ad25-14648570febe`: train 16, development 7, final 9. Independent SQL queries confirmed disjoint groups; duplicates were not discarded.                                                                                                                                                                                                               |
| Document extraction          | Native PDF and PNG OCR landed with source checksums, page/region evidence and parser metadata. Protected PDF failed with zero cells.                                                                                                                                                                                                                                           |
| Original-source export       | PDF export was byte-identical to its original; SHA-256 `b4d9ff9bc829c9ea46c0201a9495408021572f76609f34018f0269c0f323f61d`.                                                                                                                                                                                                                                                     |
| Preparation                  | `7f599974-b52a-42c1-9d46-84166096ce8c` prepared all 23 train/development decisions. Initial failure correctly identified the missing pinned development worker; explicit retry succeeded after deployment.                                                                                                                                                                     |
| Training                     | `59f371c5-3a43-4b3f-a5ba-154cf7a0f0cc`: four steps, three recorded checks, finite observed losses/gradients, exact checkpoint reload. Repeated launch returned the same job.                                                                                                                                                                                                   |
| Held-out comparison          | `77902cbb-61f7-4283-a25e-90517b3c6655`, foundation versus canary on nine final rows; preparation verified zero training overlap. Completed: both participants scored 9/9, with no invalid, incompatible or missing predictions. Samples and per-label metrics were inspected through MCP. Both had 5/9 accuracy; canary cross entropy was 0.6543 versus 0.6882 for foundation. |
| Inference                    | Historical test deployment `a7ee5df5-c61a-4695-b395-5ba79b7f47e4` reached ready. Request `9b8ec539-4905-4a8b-969b-bab52893516b` succeeded with two finite, normalized probability results; repeated requests reused the receipt, changed inputs conflicted, and malformed options were rejected.                                                                               |

The four-step synthetic canary tests lifecycle behavior, not production model
quality. Its final development assessment correctly reported missing predicted
labels and accuracy below the same-subset majority baseline.

## Completed recovery and final inference

A local container recreation exposed a mismatch between the compose defaults and
this installation's original media volume. Original artifacts remained intact.
The original mounts were restored, and the two newly created parser-test files
were copied without overwrite and verified by checksum. The comparison resumed
through MCP using its exact recorded provider call ID; no provider inference was
resubmitted. It completed with full coverage and no issues.

The new canary deployment `b0d0f0bd-ec59-4468-b219-dc0bde20e8b5` reached ready.
Inference `599dd34f-839a-42fa-8e8a-200d51839d16` succeeded for two support decisions,
preserving option order and the trained artifact identity; both distributions
were finite and summed to one within floating-point tolerance. Repeating the
same request key returned the same completed receipt. No alias was activated.

A fresh scanned-PDF upload through the isolated service published all four OCR
rows in dataset `13568cb3-c039-4dca-8298-8acf1787bfb2`, retaining original SHA-256
`f7d96a633290e0a54c06b1ae00433e7153a08f40bf4f9d27e89f1119bbb8a5c1`, page evidence,
Tesseract version and extraction provenance.

## Production prerequisites and limits

- Apply the reviewed companion Workshop infrastructure, preload approved immutable
  linux/amd64 runtime images and verify the Docker-capable ECS host. The existing
  Fargate fleet cannot run this controller. The application preflight refuses
  missing services or an unconfigured runtime allowlist.
- Use the documented drained maintenance cutover with a restorable database/media
  backup. Validation covered current main as requested; no production database
  was inspected or migrated. A production-copy rehearsal remains an operator step.
- Deploy the exact pinned Modal production releases. Development releases were
  used for E2E validation; no production provider release was changed.
- Browser/visual E2E was not run because the user requested MCP-only E2E. Existing
  UI/UX was preserved and the full frontend suite and static design checks passed.
- MCP E2E sampled synthetic tabular/text/document tasks and native decision
  training/evaluation/inference. It does not prove every provider, every data
  format, production load or model quality. The existing automated suite supplies
  broader regression coverage; no claim of zero possible regressions is made.

## Observability signals follow-up

Observability now has three direct view toggles: Sessions, Signals and Traces.
The separate Signals route/sidebar entry is removed. Switching views clears
hidden session/signal filters; selecting a finding opens its retained source
traces, with project isolation shared by REST and MCP. Deterministic checks run
by default, with explicit project/capability overrides preserved. Plan drift
remains opt-in because it makes model calls. No schema migration is required.

Validation: 36 focused backend/MCP tests, all 875 frontend tests and all six
live-scoring journeys passed; frontend type checking, lint, design/contrast/control
checks and a production build passed. The docs broken-link check passed.
Local MCP confirmed 24 default checks and saved findings for both Hermes (102)
and browser-use (104); selected findings resolved to their recorded trace evidence.

A mistakenly broad journey invocation was interrupted after encountering the
obsolete "Root traces" Console selector; both affected Console tests now use
"Traces" and the navigation test covers all three view toggles. The non-browser
live-scoring file then passed in isolation. Console/browser verification was
blocked by automatic approval review under the earlier MCP-only testing
instruction, so the updated browser tests have not been rerun.
