# Platform regression follow-up

Branch: `codex/general-decision-training`, starting at
`b5c466e00e69340f83133c3b4aee55967b5ffdc7`. Tests use localhost, disposable test
databases and `overmind-dev`. No production deployment or main push is included.
This report extends `branch-e2e-2026-10-10.md` and
`dead-features-and-remaining-tests-2026-10-10.md`.

## Failures investigated and fixed

### Trace selection and attribution

The inherited expected failure labelled a multi-capability trace with its first
capability. Relabelling alone would leave the wrong input/output pair. Expanded
network journeys reproduced incorrect sibling attribution and tool arguments
being substituted for missing wrapper input.

Trace sources now pin an extraction preference. An explicit source capability
filter takes precedence over the destination association. A unique matching
invocation scopes inputs, output, transcript, tools, usage and score together.
Repeated outer invocations fail before any cell is published. If the destination
capability is absent from the trace, the complete original source remains usable.
A mixed whole trace has no invented single-capability attribution. Missing wrapper
input/output remains missing. Source bindings retain the same extraction choice.

The expanded filter cases then found a second bug: an unlabelled trace root was
excluded even when child spans matched the requested capability. The shared filter
now matches any span in the same project and trace for the trace-head view, while
`all_spans=true` filters each span directly. Console, REST, MCP and dataset source
selection share this behavior.

Failure controls cover REST/MCP, both sibling capabilities, explicit IDs/filter
selection, captured/missing wrapper I/O, tools, scores and usage, repeated
invocations, and a source capability different from the destination. The SDK
trace journey also compares REST/MCP trace-head results and all-span filtering.

- `/tmp/platform-trace-attribution-before-retry.log`: 4 failed, 3 passed.
- `/tmp/platform-trace-attribution-after.log`: 4 failed, 4 passed.
- `/tmp/platform-trace-attribution-retest.log`: 12 passed.
- `/tmp/platform-journeys-full.log`: 51 passed, 17 failed after adding filter cases.
- `/tmp/platform-trace-filter-retest.log`: **23 passed**, no skips or expected failures.

Harness corrections were separate from the product fixes: `overmind.run` does not
capture function arguments without observation; a structural root also receives
a task execution; task-execution token totals are trace-wide. The final assertions
use explicitly observed wrappers and independently recorded model-request tokens.
The initial socket denial was a sandbox restriction; the same command ran with
scoped localhost access.

### SDK span integration coverage

The old span test hardcoded a production API/project, required live keys and
converted some HTTP failures into skips. It now runs real OpenAI instrumentation,
SDK tracing and protobuf export in a subprocess against a localhost model/OTLP
server. It verifies structured output, finish reason, token usage, capability
identity, parentage and credential separation. All default SDK test targets now
include this test.

CI no longer requires production secrets for this matrix. `--no-sync` preserves
the OpenAI version selected by the matrix, and `--upgrade` actually refreshes its
`latest` case. The test checks the subprocess package version against its parent.
OpenAI 1.70.0, 1.109.1, 2.6.1 and 2.20.0 each passed on local Python 3.13.
The full SDK suite also passed with its locked client. The complete Python
3.10–3.14 CI matrix was not run locally.

Early fixture failures were corrected after inspecting actual synthetic exports:
`force_flush_traces` returns no success boolean, finish reasons use an array, and
this SDK emits `genai.prompt_tokens`/`genai.completion_tokens`. The test now checks
those existing wire fields instead of obsolete expectations.

#### Decision Console fixture validation

Visual inspection of the passing Console screenshot found that its synthetic
fixture used unsupported `target_semantics="distribution"`. The platform correctly
rejected that data, but the test asserted only the native baseline controls. The
fixture now declares `annotator_distribution`, and the journey also requires native
probability-training details with no invalid-data or pending-validation message.
This correction strengthens the journey; it is not a change to accepted target
semantics.

That corrected fixture exposed a real REST failure: the native recommendation
shortcut returned raw dataset stats and an empty benchmark object, while the
serializer required normalized row/token fields and a snapshot date. The response
failed with `KeyError: rows`. Native and chat recommendations now share the same
dataset projection; the declared-contract source is typed, and an absent benchmark
snapshot stays null. The Console labels that source and does not render a fabricated
date. The network test compares REST with MCP readiness and requires valid native
controls and explicit missing benchmark evidence.

`/tmp/platform-console-and-redis-final.log` records the reproduced 500 (three test
bodies passed, but the browser-health teardown correctly failed). The corrected
Console and recommendation suites were then rerun. A follow-up selector timed out
on an inline text fragment despite the retained browser text containing it and zero
browser/server errors; a non-exact text locator fixes that harness assertion.
See the final results below.

## Running development schema

The sole remaining migration was `0035_retire_cursor_usage`. A private mode-0600
PostgreSQL custom-format backup was saved at
`/tmp/platform-regression-db-backup/overbae-before-0035.dump` before applying it to
the local `overbae` database. `pg_restore --list` verified the backup is readable,
and `migrate --check` found no pending migrations. The migration succeeded, and authenticated MCP
project discovery still returned the localhost catalogue afterward. The earlier
isolated migration checks exercise forward archive, reverse restoration and
collision rejection. Historical billing receipts remain readable.

## Development native evaluation

The user's renewed development-testing authorization covered the previously
pending evaluation-worker deployment. Command:

```sh
PYTHONPATH=overbae MODAL_ENVIRONMENT=overmind-dev .venv/bin/modal deploy --env overmind-dev overbae/modal/modal_decision_evaluation.py
```

It deployed `overmind-decision-eval-ba6230f3b2b98765ed6d1052` to `overmind-dev`.
The pinned release is
`ba6230f3b2b98765ed6d1052b13c769e9b624ac963852a4e5f2e112a135ee850`.
`/tmp/platform-dev-evaluation-deploy.log` records successful deployment.

MCP `schedule_native_evaluation` used the exact arguments retained in
`platform-native-evaluation-2026-10-10.json`. Evaluation
`598e53a5-a271-4aae-a399-49f433326717` completed for the existing four-step Titanic
canary `76f061a9-4214-43c8-b620-401923382b17`: 89 frozen calibration rows and 89
final rows, with zero exact/group contamination. Every final row was scored for
both base and trained candidate; no missing, invalid or incompatible predictions.

| Raw metric    | Qwen3 0.6B base | Four-step candidate |
| ------------- | --------------: | ------------------: |
| Accuracy      |        33.7079% |            66.2921% |
| Cross entropy |        0.695094 |            0.679573 |
| Brier score   |        0.501947 |            0.486434 |

Calibration retained the same accuracies; calibrated cross entropy was 0.693166
and 0.645105 respectively. This is a pipeline canary: candidate accuracy matches
the majority-class share, so the result does not establish useful classification
quality. Recorded evaluation compute estimates total about $0.2113; that is not an
all-in invoice or a new training charge.

Repeating the exact scheduling request returned the same plan and all six existing
provider call IDs, without another submission. The authenticated report download
is 99,907 bytes, SHA-256
`73ed61e6e4b0afaaa5e06f6cf68e7da571dd5ff787c67f055a7a1075c5b7e946`.
Downloaded metrics and benchmark-aggregated coverage agree with MCP. The first
checker assumed coverage lived at the download's top level; it was corrected to
aggregate the report's per-benchmark counts and rerun successfully.

The read-only parity command is repeatable:

```sh
.venv/bin/python tests/evidence/platform_native_evaluation_check.py --receipt tests/evidence/platform-native-evaluation-2026-10-10.json --output /tmp/platform-native-evaluation-parity-replay.json
```

It uses the endpoint-bound saved localhost account connection, rejects redirects,
and never writes credentials into a receipt. Preserve the committed receipt when
replaying. No paid request is needed to repeat this verification.

## Repeatable regression commands

The local Compose stack supplies PostgreSQL and Redis. Test providers are
scripted; the restricted Workshop container executes actual retained packages.
A temporary settings module avoids the shared default disposable database name:

```python
from tests.settings import *

DATABASES["default"]["NAME"] = "branch_e2e_20261010"
```

Save that as `/private/tmp/branch_e2e_settings.py`. From the repository root:

```sh
PYTHONPATH=/private/tmp:. WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea .venv/bin/pytest tests/ -n 4 --dist worksteal -q -rs --no-cov --ds=branch_e2e_settings
PYTHONPATH=/private/tmp:. TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest tests/journeys/ -q -rs --no-cov --ds=branch_e2e_settings
PYTHONPATH=/private/tmp:. TEST_REDIS_URL=redis://localhost:6379/15 WORKSHOP_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest tests/test_task_lock.py -q -rs --no-cov --ds=branch_e2e_settings
```

The isolated optional-dependency environment is
`/private/tmp/overmind-remaining-tests-20261010`: Python 3.13, Torch 2.10,
Transformers 5.17, PEFT 0.21.2 and Truss 0.18.32. It exists only to run the ML and
Baseten checks unavailable in the lightweight Django environment:

```sh
PYTHONPATH=/private/tmp:. /private/tmp/overmind-remaining-tests-20261010/bin/python -m pytest tests/test_weight_ops.py tests/test_sft_token_accuracy.py tests/test_finetuning_baseten_runner.py -q -rs --no-cov --ds=branch_e2e_settings
```

The final optional-dependency rerun also included `tests/test_dataset_land_traces.py`
and used `/private/tmp/platform_optional_settings.py` with database name
`platform_optional_20261010`, so it did not share the concurrent journey database.
It passed all 56 cases, including the final root-selection fallback.

From `overmind/`:

```sh
uv run --extra tracing --group dev python -m pytest tests/ -q
make lint-check
uv run --extra tracing --group dev --with openai==1.70.0 python -m pytest tests/test_spans.py -q
```

Repeat the last command with 1.109.1, 2.6.1 and 2.20.0 for the locally exercised
client matrix. From `frontend/`, run `bun run test`, `bun run typecheck`,
`bun run lint` and `bun run check:all`. Run `make generate_api_client` from the
repository root after backend surface changes. Pre-commit is restricted to the
explicit task file list.

## Coverage boundaries

Network/Console journeys exercise boot, authentication and project isolation,
CLI scan/sync/provenance, OTLP and vendor connector ingestion, trace scoring and
observability, file/trace datasets, frozen consumer versions, evaluations and
judges, training/benchmark/deployment/activation, inference, Optimiser patch
selection, billing and recovery. Browser cases cover dataset creation, decision
controls, cancellation evidence and all main project routes. Broader backend tests
cover pipeline runtime/lineage/recovery, data-first partition/comparison/experiment
workflows, monitoring, estimates, requests, MCP contracts/resources/prompts and
negative permission/provider paths. Frontend and SDK suites cover their respective
components and command contracts.

External providers are simulated in the repeatable network suite; real Unsloth GPU
training, checkpoint recovery and the new held-out native evaluation have separate
development receipts. Banking77, SST-5 and BoolQ saved comparisons were reverified
against their exact report bytes and all 15 participants' raw/calibrated summaries;
those are retained benchmark results, not newly retrained benchmarks in this pass.
The RS-LoRA merge fix passed numerical tests but is not a claim that every serving
image or external provider deployment was refreshed. Main and production remain
untouched. These checks cannot prove the absence of every possible regression.

## Final observed results

| Check                                         | Result                                                                                                       | Log                                                        |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------- |
| Full backend                                  | 4,306 passed; 7 environment skips, separately covered below                                                  | `/tmp/platform-backend-final.log`                          |
| Optional ML/Baseten plus final trace assembly | 56 passed, zero skips                                                                                        | `/tmp/platform-optional-final.log`                         |
| Recommendation, context and MCP follow-up     | 303 passed                                                                                                   | `/tmp/platform-recommendation-backend-retest.log`          |
| Full frontend                                 | 867 passed in 116 files                                                                                      | `/tmp/platform-frontend-final.log`                         |
| Full SDK                                      | 591 passed                                                                                                   | `/tmp/platform-sdk-final.log`                              |
| Complete network/Console suite                | 68 passed, zero skips or expected failures                                                                   | `/tmp/platform-journeys-final.log`                         |
| Strengthened native Console/REST/MCP journey  | 1 passed after the late recommendation fix                                                                   | `/tmp/platform-native-console-final.log`                   |
| Console/Redis follow-up                       | Other 5 Console journeys and both Redis checks passed; native text-locator failure resolved by the row above | `/tmp/platform-native-console-retest.log`                  |
| Typecheck, lint and design/contrast/controls  | Passed                                                                                                       | `/tmp/platform-{typecheck,frontend-lint,design}-final.log` |
| SDK lint/format                               | Passed                                                                                                       | `/tmp/platform-sdk-lint-final.log`                         |
| OpenAPI regeneration                          | Passed                                                                                                       | `/tmp/platform-native-recommendation-api-client.log`       |
| Model/migration consistency                   | No missing model migration; existing branch history valid                                                    | `/tmp/platform-migrations-final.log`                       |
| Development native report replay              | Passed; exact bytes, metrics and coverage                                                                    | `/tmp/platform-native-report-replay.log`                   |

`platform-regression-results-2026-10-10.json` retains log hashes and the latest
passing receipt for each of the 68 network/Console cases. The full journey suite
passed before the later native recommendation fix; the entire Console suite was
then rerun, and its one locator correction was independently retested. The late
recommendation change also passed the 303-case backend follow-up and full frontend
suite. Counts from overlapping suites must not be added as unique tests.

Browser screenshots and diagnostics are under `tests/journeys/.runs/`, especially
`20261010T230915Z`, `20261010T231715Z` and `20261010T231831Z`. The final decision
setup screenshot was visually inspected: valid decision data, row/token summary,
no fabricated benchmark evidence, and no page or server errors. Test-only short
JWT-key, dependency deprecation and frontend runner warnings remain warnings;
none is counted as a failed product check.

Every original skip is accounted for. Five optional-dependency entries are covered
by the 48 ML/Baseten cases within the 56-case run; two Redis entries passed against
the real local test broker. The obsolete PII subsystem's three checks were removed
with that unused code, not represented as successful tests.

The explicitly adopted checkpoint-transfer journey is included in Git. Hashes of
the other 205 original untracked files match the initial snapshot; they remain
untracked. Temporary environments, database backups, local logs, browser output
and the bulk BoolQ archive are excluded. The evidence scripts depend only on
tracked reference receipts and the documented local services.
