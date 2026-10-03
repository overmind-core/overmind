# Adaptive Data Workshop preparation

The Workshop must investigate the user's objective and actual rows before choosing a transformation. A saved, inspectable plan records the source fingerprint, interpretation, families, field mappings, assumptions, unresolved questions, steps and applicable checks. Executed cells retain the plan that produced them. Plans do not approve semantic edits or make quality findings launch blockers.

MCP classification: MCP-ready through the existing dataset chat, inspection and run operations. The internal plan tool shares domain services with REST/Console serialization; no new public mutation is required. The Console must display the plan and separate technical, preservation, coverage and semantic findings. The existing Jev pilot remains pinned and is observed independently.

Failure cases to verify before implementation:

- Automatic preparation rewrites an unfamiliar source before reading the user request or inspecting its rows.
- Renamed or nested input/target columns cannot be mapped without hard-coded aliases; a mapping overwrites a conflicting existing field, changes class order, loses weights or leaks targets into requests.
- A plan references missing columns or the wrong project/version, survives a changed source or intent as current, or silently grants approval to exclusions/target changes.
- A new plan rewrites the provenance of an earlier prepared/used version; completed steps are claimed without an executed cell.
- Quality requires unrelated universal checks, counts unmeasured checks as passes, or confuses technical readiness with verified semantics.
- Semantic judging ignores the plan, runs without applicable checks, or exceeds its declared row budget.
- REST and MCP disagree on the plan/assessment, and Console users cannot inspect the same state.

## Repeatable verification

Environment: repository Python environment (`uv`), local PostgreSQL/test settings, existing Docker Compose API and workers, Bun frontend, Overmind CLI with the project's saved credential, and a configured Workshop engine. No training launch is part of these checks. Never put API keys in the fixture or logs.

Fixtures: `adaptive-workshop-fixture.jsonl` and `adaptive-workshop-declared-schema.jsonl` in this directory. Each contains nine synthetic rows across intent, sentiment and evidence-QA tasks, with two/three-option soft distributions, valid blank context, repeated observations and nonuniform weights. The second explicitly distinguishes example weights, shared case identity and coverage strata. These are engineering fixtures, not benchmark ground truth.

Commands run from the repository root:

```sh
uv run pytest tests/test_workshop_planning.py -q
uv run pytest tests/test_workshop_planning.py tests/test_dataset_agent.py tests/test_dataset_preparation.py tests/test_dataset_context.py tests/test_decision_workshop.py tests/test_workshop_readiness.py tests/test_dataset_workflow_safety.py tests/test_mcp_datasets.py tests/test_mcp_resources.py tests/test_mcp_finetuning.py tests/test_dataset_streaming.py -q
uv run pytest tests/test_workshop_planning.py tests/test_dataset_split.py tests/test_finetuning_split.py tests/test_dataset_workflow_safety.py tests/test_training_preparation.py -q
uv run pytest tests/test_workshop_planning.py tests/test_dataset_api.py tests/test_dataset_contract.py tests/test_dataset_dispatch.py tests/test_mcp_datasets.py tests/test_dataset_preparation.py -q
make generate_api_client
make check-migrations
overmind dataset upload docs/experiments/adaptive-workshop-declared-schema.jsonl --project-id 1e3f3e92-b50d-4590-85ed-97921d132d3c --intent eval --json
```

Frontend commands from `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
bun run test src/components/finetuning/train/__tests__/setup-panel.test.tsx
```

Poll the returned dataset ID through MCP `get_job(kind=dataset_run)`, then `inspect_dataset`. Query source and active cells with `query_dataset`, SQL `SELECT * FROM t ORDER BY source_row`, limit 20. Decode JSON-valued columns before comparison. Check every row's identity, question, state, ordered options, full distribution, weight, inherited case group and absence of references from model inputs. Open its Console notebook and expand Preparation plan.

## Observed results — 2026-10-02

- The initial five workflow tests failed before implementation, then passed. The later landing-purpose cases failed for both transcript and input/reference shapes before removing automatic purpose inference.
- Broad Workshop/MCP regression: **247 passed**. Split/training handoff regression: **71 passed**. Final purpose/REST/MCP regression: **121 passed**. These suites overlap; do not sum them as distinct tests.
- Frontend training setup: **17 passed**. Typecheck, lint, design/contrast/control checks passed. Contrast checks cover both themes; the live browser inspection used the current dark theme.
- API client regenerated. Migration `0013_dataset_preparation_plans` applied locally. `makemigrations --check --dry-run` reported no changes. `check-migrations` passed against the available `origin/main`, but its Git comparison does not include untracked migrations; no rebase or staging was performed.
- Existing test warnings: short local JWT test signing key, a streaming response consumption warning, and Vitest's local-storage path warning.
- First live run exposed unclear mapping field guidance and confusion between coverage fields and case identities. The tool schema now documents these roles explicitly. Automatic repairs append cells to preserve plan attribution.
- Second live run: dataset `80ef4dae-d54b-4cbe-8145-11d57f5b8c1d`, source `9b7dd4ae-e41a-4971-aef5-9bb1b3230c0d`, active `019d2ff0-a1ab-4807-b770-6218bf75d9ef` (`1.2`), plan `2d3c2b07-2d24-441d-a351-ccf26b176077`. The agent explored first, saved a plan, and ran two cells. All nine rows and their weights, option order, targets, input evidence and case identities were preserved. MCP returned untruncated rows. Independent source/output comparison found **zero mismatches**, recorded in `adaptive-workshop-live-result.json`.
- The final assessment is technical **pass**, preservation **pass**, coverage **pass**, semantic **unknown**. Semantic budget and reservations both remained zero. This verifies no unwanted judging on this fixture; it does not establish publisher truth or qualify every possible source schema.
- Browser inspection confirmed the expanded plan shows families, arbitrary mappings, case versus coverage fields, applied cells, checks, audit budget, assumptions and unresolved questions.

The new natural-language intent input is deliberately outside this change. Existing selected intent and chat context can inform a plan; an omitted purpose stays pending during exploration. The running Jev quality pilot remains pinned to its original training inputs and is observed separately by the five-minute thread monitor.
