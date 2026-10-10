# Dead-feature audit and remaining test coverage

Checkout: `codex/general-decision-training`, starting commit
`b5c466e00e69340f83133c3b4aee55967b5ffdc7`. This follows the PII removal recorded in
`pii-service-removal-2026-10-10.md` and the broader qualification recorded in
`branch-e2e-2026-10-10.md`. No cloud deployment or resource deletion was performed for this follow-up.

## Removed with caller evidence

| Remnant                                     | Evidence                                                                                                                                                           | Removal and retained behavior                                                                                                                                                                                                                                                                      |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Server-side Cursor agent bridge and billing | Import/caller analysis found no bridge startup. A subsequent producer audit found usage written only by demo seeding and tests.                                    | Removed the SDK, shutdown hook, helpers, key configuration, model/pricing catalogue entry, billing callbacks, billing enum and demo usage. Old usage is archived before its field is dropped; ledger receipts remain unchanged.                                                                    |
| Console chat-turn activity timeline         | Reachability from `main.tsx` and every route found `turn-steps.tsx` reachable only from its own test. Its activity/thought renderer had no other product consumer. | Removed `TurnSteps`, `ActivityTimeline` and their unused timeline types/builders. Moved the live tool formatter/icon map to `components/traces/tool-detail.tsx`; conversation details still import them. Kept the shared elbow-list primitive and moved its two existing geometry tests beside it. |

The audit checked imports, exports, configuration, test callers and dynamic-name
references; a text-search result alone was not treated as proof. Recommendation
modules initially appeared unused until relative imports were resolved and were
retained. Existing LLM tool helpers with tested timeout behavior were also retained.
The obsolete usage column is removed by the reversible archival migration below.
Existing migrations, historical traces and billing receipts remain intact.

The `IoBlock` and `toolDetail` function text is identical before and after moving
it. SHA-256 of that combined text, stripped only of trailing whitespace:
`a731a9f6eb20e4ff587ac2f9803dfe683aa26f8543d6404231c25a32bf8177a8`.
`TOOL_ICONS` is also unchanged. Historical tool names remain readable.

Review the original references with the starting commit, and repeat the search
against the working tree after deletion:

```sh
git grep -n -E 'cursor_sdk|cursor_usage|CURSOR_API_KEY|ActivityTimeline|TurnSteps|buildTimelineSteps' b5c466e00e69340f83133c3b4aee55967b5ffdc7 -- overbae tests frontend .agents docker-compose.yml pyproject.toml
rg -n 'cursor_sdk|cursor-sdk|CURSOR_API_KEY|cursor_usage|ActivityTimeline|TurnSteps|buildTimelineSteps' overbae tests frontend .agents docker-compose.yml pyproject.toml
```

Remaining `cursor_usage` references belong to historical migrations, the new
archival migration and preservation fixtures. No active producer or billing
consumer remains. Evidence documents and historical test names can also match. The finding is scoped to these two remnants, not a claim that
every unreferenced function in the repository is safe to delete.

## Previously skipped ML and Baseten checks

The five dependency-related skip entries included one entire weight-operations
module, two decoder cases and two Baseten cases. Installing their dependencies
allowed all three files to execute: **48 passed, zero skipped** in 4.66 seconds.
This is 48 cases across those files, not 48 previously reported skip entries.

Environment: isolated `/private/tmp/overmind-remaining-tests-20261010`, Python
3.13.12, Torch 2.10.0, Transformers 5.17.0, PEFT 0.21.2, safetensors 0.8.0 and
Truss 0.18.32. `truss_train` is supplied by Truss. Baseten SDK tests load the
generated configuration and read a temporary credentials file using dummy values;
they do not launch provider jobs. Decoder checks use tiny local Qwen3/Qwen3.5
models and compare token accuracy and gradients with full-logit calculations.
Weight checks create local safetensors fixtures, including dense and fused expert
adapters, without downloading checkpoints.

```sh
uv venv --python .venv/bin/python /private/tmp/overmind-remaining-tests-20261010
VIRTUAL_ENV=/private/tmp/overmind-remaining-tests-20261010 uv sync --active --frozen --group test
uv pip install --python /private/tmp/overmind-remaining-tests-20261010/bin/python 'torch==2.10.0' 'transformers==5.17.0' 'peft==0.21.2' 'safetensors==0.8.0' 'truss==0.18.32'
PYTHONPATH=/private/tmp:. /private/tmp/overmind-remaining-tests-20261010/bin/python -m pytest tests/test_weight_ops.py tests/test_sft_token_accuracy.py tests/test_finetuning_baseten_runner.py -q -rs --no-cov --ds=branch_e2e_settings
```

Use the temporary Django settings shown below. Setup logs are
`/tmp/remaining-tests-{env-setup,env-sync,extra-install}.log`; final result is
`/tmp/remaining-tests-final.log`.

The optional SDK install changes dependencies only in this disposable environment.
Its package metadata check reports Truss's Rich 13 requirement conflicting with
the installed Overmind SDK's Rich 14 minimum, and the repository's existing
Cryptography override conflicting with Clerk's declared upper bound. These are
not resolved by the passing scoped tests. The normal application environment was
used separately for broad regression; this temporary combination is not a
proposed application dependency set.

## Failures investigated and fixed

1. The old unknown-config test used `monteclora_config` and `velora_config` as
   invented extension keys. Current PEFT recognizes those names, so the made-up
   values caused recursive configuration loading. Replaced them with a genuinely
   unknown test key and exercised the public merge workflow, checking that the
   original adapter config remains byte-identical.
1. That stronger fixture exposed incorrect merging of rank-stabilized LoRA:
   both dense and fused-expert paths multiplied by `alpha / rank` instead of
   `alpha / sqrt(rank)`. Added the expert case before fixing production code;
   both variants failed numerical tensor comparisons. Both paths now honor
   `use_rslora`, while ordinary LoRA keeps its original scale.
1. The first scoped journey command named a nonexistent Optimiser journey file.
   No tests ran. Corrected the selected paths and reran; all six journeys passed.
   Optimiser billing and model-registry coverage is in the backend suite.

Failure receipts: `/tmp/remaining-tests-first.log` (1 failed, 46 passed),
`/tmp/remaining-tests-retest.log` (1 failed, 46 passed), and
`/tmp/rslora-merge-before.log` (2 failed, 11 passed). The final three-file run
passes all 48 cases. The LoRA fix is local code; existing deployed workers need a
new release before they use it.

## Cleanup regression checks

Requirements: installed backend/Bun dependencies, existing Compose Postgres and
Redis, Chromium, Docker access and the restricted Workshop runtime image below.
The backend suite runs without the removed `cursor-sdk` package. Use separate
suite invocations so they do not share a running disposable test database.

`/private/tmp/branch_e2e_settings.py`:

```python
from tests.settings import *  # noqa: F403

DATABASES["default"]["NAME"] = "branch_e2e_20261010"
```

```sh
uv pip uninstall --python .venv/bin/python cursor-sdk
PYTHONPATH=/private/tmp:. WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea .venv/bin/pytest tests/ -n 4 --dist worksteal -q -rs --no-cov --ds=branch_e2e_settings
PYTHONPATH=/private/tmp:. TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest tests/journeys/console/test_console.py::test_the_console_shows_the_synced_agent_and_its_traces tests/journeys/test_periodic_tasks_recover.py tests/journeys/test_agent_model.py -q --no-cov --ds=branch_e2e_settings
```

From `frontend/`, run `bun run test`, `bun run typecheck`, `bun run lint` and
`bun run check:all`.

| Check                                                   | Observed result                                     | Receipt                                                   |
| ------------------------------------------------------- | --------------------------------------------------- | --------------------------------------------------------- |
| Backend without Cursor SDK, before billing retirement   | 4,305 passed, 7 environment skips in 118.21 seconds | `/tmp/dead-features-backend.log`                          |
| Console/MCP/worker journeys                             | 6 passed in 16.16 seconds                           | `/tmp/dead-features-journeys.log`                         |
| Frontend                                                | 867 passed in 116 files                             | `/tmp/dead-features-frontend-tests.log`                   |
| Frontend typecheck, lint, design, contrast and controls | Passed                                              | `/tmp/dead-features-{typecheck,frontend-lint,design}.log` |

Frontend count decreased by six tests belonging exclusively to the deleted
timeline. The two shared geometry tests remain. Journey receipts and the visually
inspected Observability screenshot are in
`tests/journeys/.runs/20261010T223504Z/`. Browser evidence records dark rendering,
zero page exceptions and zero server errors. The fixtures sync the support-desk
agent through CLI/MCP, ingest a real SDK trace into the isolated API, render the
trace list and exercise worker recovery. Provider responses are faked.

The Redis follow-up was repeated after cleanup: **2 passed, zero skipped** in
5.20 seconds (`/tmp/dead-features-redis.log`). The broad backend run still lacks
optional ML/Baseten packages and uses an in-memory broker, so it reports seven
environment skips. These are exactly the five entries covered by the separate
48-case run plus these two Redis cases; none is counted as a broad-suite pass. The
three PII skips were retired with the unused service, not claimed as passing.
Together with the 48-case dependency run, every entry from the original ten-skip
report is accounted for. The later platform follow-up fixes the multi-capability trace attribution failure
and completes the development native evaluation. See
`platform-regression-2026-10-10.md`; neither feature was removed.

## Retired billing follow-up

The original conservative pass retained Cursor billing because completion/failure
callbacks still invoked it. Following the data producers established that those
callbacks were themselves obsolete: only tests and demo seeding populated
`OptimizerExperiment.cursor_usage`. Current native-agent Optimiser operations do
not submit or accumulate server-side Cursor SDK usage.

Removed those callbacks, their helper, `Vendor.CURSOR`, `composer-2.5`, its unused
pricing-substitution mechanism, `BillingService.CURSOR_AGENT` and synthetic demo
usage. Native Cursor IDE/MCP support and externally supplied model provenance
remain valid features and are unaffected.

Migration `0035_retire_cursor_usage` archives nonempty usage into the experiment's
`state.archived_cursor_usage` before dropping the field. It retains unrelated
state and timestamps, refuses to overwrite an existing archive, and restores the
original field on rollback. It never edits a ledger row or creates a charge.
Migration application and rollback were tested in disposable PostgreSQL databases.
The platform follow-up also applied it to the local development database after a
private database backup; no production schema was changed.

Billing history now exposes service identifiers as read-only strings, with a
general label fallback for retired services. Old `cursor-agent` entries still
read as “Cursor agent”; the active enum no longer advertises that service. The
OpenAPI client was regenerated with `make generate_api_client`. MCP uses the same
Optimiser domain services; no MCP tool or result schema changed.

Failure cases covered: loss of usage or integer precision while dropping the
column; damage to unrelated experiment state; archive-key collisions; rollback
loss; repricing or duplicate charging of old receipts during completion; unreadable
historical service identifiers; and failures in current MCP Optimiser operations.

```sh
PYTHONPATH=/private/tmp:. .venv/bin/pytest tests/test_retired_cursor_usage_migration.py tests/test_optimizer_ledger.py tests/test_llm_and_modal_billing.py tests/test_model_registry.py -q --no-cov --ds=branch_e2e_settings
PYTHONPATH=/private/tmp:. TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest tests/journeys/test_credits.py tests/journeys/test_the_loop.py -q --no-cov --ds=branch_e2e_settings
PYTHONPATH=/private/tmp:. TEST_REDIS_URL=redis://localhost:6379/15 WORKSHOP_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest tests/test_task_lock.py -q -rs --no-cov --ds=branch_e2e_settings
make check-migrations
DJANGO_SETTINGS_MODULE=tests.settings make generate_api_client
```

The ledger-read journey passed before the change (`/tmp/cursor-retirement-ledger-before.log`).
The targeted migration/billing suite now passes **29 cases** in 12.81 seconds
(`/tmp/cursor-retirement-targeted-final.log`). Two fixture errors were corrected:
missing the required capability, and expecting the original winner score after
completion legitimately recalculated it. The final rollback check compares the
actual completed state. The failed runs remain in
`/tmp/cursor-retirement-targeted{,-retest}.log`.

Django reports no missing migrations, the repository migration check passes, and
frontend typechecking passes. The numbering script checks committed migrations;
`0035` is additionally checked against the working tree and its `0034` dependency.
Logs: `/tmp/cursor-retirement-{migrations,client,typecheck}.log`.

The final billing/MCP journeys passed **4 tests** in 37.24 seconds
(`/tmp/cursor-retirement-journeys.log`). Receipts are in
`tests/journeys/.runs/20261010T224216Z/`; they include the complete native-agent
Optimiser loop and ledger history, hosted credits and self-hosted usage. The old
Cursor credential entry was also removed from `.env.example`.

A tracked-source search for `cursor_sdk`, `cursor-sdk`, `CURSOR_API_KEY`,
`CURSOR_AGENT_MODEL` and `CURSOR_AGENT`, excluding historical migrations and
qualification documents, now returns no matches. Scoped pre-commit checks pass.
All 206 pre-existing untracked files remain byte-identical to the starting hash
snapshot at validation time. Commit preparation subsequently includes and formats
the previously untracked checkpoint journey; the other 205 files remain unchanged.

Final backend run after billing retirement: **4,306 passed, 7 environment skips**
in 123.05 seconds, with Cursor SDK absent
(`/tmp/cursor-retirement-backend.log`). The seven entries are the same optional
ML/Baseten and Redis checks already passed in the configured follow-up runs.
There are no outstanding test entries from the original ten-skip report. The
three obsolete PII checks were removed with their disconnected feature; they are
not represented as executed passes.
