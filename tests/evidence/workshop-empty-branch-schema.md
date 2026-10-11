# Workshop empty-branch schema

Scope: platform execution and metadata only. The existing native-agent-authored
review logic, recipes, source data and published user outputs remain unchanged.

Failures to exercise before changing the implementation:

- An empty script result declares `source_row`; publication appends it again,
  making SQL invent `source_row_1` and REST expose duplicate column names.
- An empty result omits `source_row`; publication must still supply its identity
  column without inventing any undeclared data columns.
- An empty result declares data columns; their names/types must survive without
  borrowing an unrelated source schema.
- A fix changes nonempty values, recipe bytes, original cells or branch membership.

The isolated publication test supplies an empty completed-script handoff because
existing declarative empty-branch tests do not exercise packaged-script schemas.
Live verification must also execute the unchanged retained package in the approved
runner and inspect its output through MCP and the installed export CLI.

Original reproduction: dataset `9fcbba7f-cb63-4024-af85-a1629fe3fb7b`, cell
`feee71da-7e31-4ad1-802e-19286735d306`, project
`e18b29b5-915d-45a7-80cd-77ffe6559205`. MCP `query_dataset` with
`SELECT * FROM t` returns zero rows with columns `source_row, source_row_1`.

The recipe's `None`-only review check is a separate coding-agent error. No evidence
establishes that Overmind caused that choice. Correcting it is out of scope.

The broader MCP regression also found a response-budget failure for 25 source
documents, each with 2,000 OCR page references: 32,108 bytes under the test's
serialization. Size checks used compact unescaped JSON, not the JSON encoding used
by clients. Recheck ASCII and Unicode filenames, complete extraction metadata and
forward-moving source cursors before changing the serializer.

## Implementation

- Empty packaged-script outputs build the declared schema with exactly one integer
  `source_row`. No undeclared user columns or transformation rules are inferred.
- Dataset inspection measures escaped JSON when deciding how many whole cells and
  source entries fit. Source paging retains complete OCR/extraction facts.
- MCP-ready: the existing inspection, query and pipeline tools expose both fixes.
  Tool schemas, catalogue identity, REST schemas and the generated client are unchanged.
  No new agent guidance, semantic logic, UI changes or dependencies were added.
- Immutable historical frames are not rewritten. New executions use the corrected
  platform behavior; original recipes and default user-selected versions are retained.

## Repeatable checks

Use the existing local Compose API/database and Workshop runner. The runner needs
the updated `workbench.py` loaded; restart it only when idle. MCP and the installed
CLI use the saved address-bound account connection; no credentials are embedded in
the fixture. The live test uses local MCP for operations and the CLI for export.
Its three real-data fixtures and retained recipe IDs come from
`workshop-real-visible-scripts.json`.

```sh
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_reusable_workshop_journey.py tests/test_workshop_mcp_acceptance.py tests/test_workshop_package_transfer_journey.py -q --tb=short
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_workshop_mcp_acceptance.py tests/test_mcp_datasets.py tests/test_mcp_resources.py tests/test_mcp_result_compat.py -q --tb=short
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_platform_replay.py --run empty-schema-verified-20261009 --report tests/evidence/workshop-platform-replay-results.json --cli /Users/tyleredwards/.local/bin/overmind
```

The live command creates separate regression datasets, never updates the original
dataset, and never saves/revises an agent-authored recipe. Each case previews three
rows and publishes the whole source twice. It recovers identical run request keys,
compares every step's exported values and retained script against the prior run,
queries every output schema and verifies the original source fingerprint, cells,
active selection and recipe fingerprint are unchanged. New `_overmind_` execution
provenance is excluded from the value comparison; `source_row` remains included.
Successful replay is evidence of execution fidelity, not semantic correctness.

## Observed checks — 2026-10-09

- Before the schema fix: **2 failed, 1 passed**. Failures reproduced the spurious
  `source_row_1`; the undeclared-identity case already worked.
- After the schema fix: **36 passed, 3 skipped, 1 failed** across the first three
  suites. The remaining failure exposed the separate MCP response-budget bug.
  Skips are optional Docker tests; the live runner checks exercise actual containers.
- Before the response-budget fix: both ASCII and Unicode metadata cases failed,
  at 32,111 and 33,537 bytes respectively with escaped JSON.
- After the response-budget fix: **55 passed** across Workshop MCP acceptance,
  dataset tools, resources and result compatibility. All 25 document inventories
  and their 2,000-page extraction lists survived pagination unchanged.
- The first live replay completed a document preview and two full publications,
  including correct empty schemas and unchanged user values. Its final source
  re-export hit the CLI's expected existing-path protection: a harness error,
  not a platform error. The harness now allocates a fresh path for every export.
  Initial receipts remain in `workshop-platform-replay-initial.json`.
- Final live matrix: **9/9 executions passed** (three previews, six full
  publications) across document evidence (320 source rows → 7 pages), tabular
  data (891 rows) and chat records (1,000 rows). Every published step's script and
  user values matched the prior run. All empty schemas contain exactly one
  `source_row`; SQL and inspection report matching column inventories.
- Original cells, selected versions, source fingerprints and recipe fingerprints
  remained unchanged for all three cases. Results and exact run/cell identifiers
  are retained in `workshop-platform-replay-results.json`.
- Full publication plus inspection/export verification took 18.4–25.1 seconds per
  replay. Previews took about 15.2 seconds, including the returned five-second
  polling interval; these are not isolated compute timings.
- Ruff, scoped pre-commit checks and `git diff --check` pass. No frontend code or
  API schema changed, so frontend suites and client generation were not run.

No paid operations, browser operations or source/recipe repairs are part of these checks.
