# Reusable Workshop pipelines

## Implementation contract

Project-owned transformations have immutable revisions. A revision contains ordered
declarative steps or an uploaded Python package, pinned runtime, parameters and
input/output requirements. A derivative records its parent revision; a revision
can execute against another dataset in the same project. Names never establish
identity or compatibility. Native agents author and explain; platform services
retain, validate, execute and report facts.

One successful execution publishes one cell per logical step atomically. Preview
retains step receipts and bounded samples without selecting a dataset version.
Failures, cancellation and unknown execution ownership cannot publish partial
chains. Existing training and evaluation cells remain immutable.

Script execution uses a dedicated controller and restricted Docker containers,
not Python inside API/Celery. Containers have no network, credentials, host mounts
or Docker socket, and have bounded CPU, memory, processes, scratch and time.
Only operator-approved immutable image identities may run. Uploaded archives are
validated without importing or executing their contents. Dependency installation
is not part of execution; dependencies belong to the pinned runtime image.

Bindings select an exact revision, source and destination. Creation is paused;
enabling is explicit and concurrency-checked. Source changes are processed as
complete snapshots initially, including late trace/score changes and removals;
this avoids pretending arbitrary scripts are incremental. Publication and the
binding checkpoint commit together. Empty output is a valid measured result.

MCP impact: lifecycle and inspection are MCP-ready. Package bytes are CLI-guided.
Console reuses the existing table and cells, with a grid-snapped canvas and a
version chip beside the name. Revisions expose native-agent-authored flow explicitly.

## Failure cases to verify before implementation

- Cross-project pipeline, package, source, binding and resource access.
- Same request key with identical inputs versus changed recipe/parameters/source.
- Cross-dataset reuse; incompatible source; attributed derivative; frozen original.
- Archive traversal, symlinks, duplicate entries, expansion limits, invalid Python,
  missing entrypoints, mutable/unapproved runtime and unsupported schema contracts.
- Multi-step success; failed later step; bad parent identities; empty output;
  preserved duplicates, nested targets, distributions, weights and row order.
- Bounded preview versus publication, deterministic repeated runs and large files.
- Cancel before/during execution; controller interruption; stale ownership;
  missed dispatch; unknown container acknowledgement; no duplicate publication.
- Container network/host/credential isolation and resource enforcement.
- Paused binding, explicit enable, no-op poll, new source, late trace/score updates,
  removed matches, failure/checkpoint preservation and revision adoption.
- Full structured MCP results, resource pagination, no read-side execution,
  factual operation events, missing-runner status and clear recovery.
- Migration of existing recipes and exact historical receipts without data loss.

## Flow-canvas acceptance cases (before implementation)

The requested canvas must retain the existing NotebookCell markup and controls.
Replace the run bar with a breadcrumb-adjacent version chip. Selecting an iteration
is read-only; restoring selects its exact immutable cell, without rerunning scripts
or changing training/evaluation snapshots. Cells snap to a 20px grid; saved positions
are local to this project/dataset. Dragging never changes execution dependencies.

Verify the unchanged table, script, export, quality and active-cell controls; folder
navigation; keyboard focus; pan/zoom; desktop/mobile and both themes. Connections
must follow exact recorded parents, not adjacent displayed versions. Test forks,
joins, repeated fingerprints and missing parents independently because ordinary
linear live data cannot reveal invented graph edges. Conditions are only displayed
when explicitly recorded; Python control flow is not guessed.

## Environment and observed results

2026-10-09: current checkout, existing Compose deployment, PostgreSQL, Redis and
dedicated Workshop controller. Pre-existing edits were preserved. The initial
sandboxed Docker check was denied; the approved host check reached Docker. It was
not a daemon outage. No paid training, evaluation or inference was started.

Approved local image:
`sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea`.
Build/configuration instructions are in `docs/workshop-pipelines.md`. Apply
migrations and run the `workshop` Compose profile before the live replay.

### Backend, MCP and real-container regression

```sh
set -o pipefail
WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea \
WORKSHOP_DOCKER_SOCKET=/Users/tyleredwards/.docker/run/docker.sock \
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync pytest \
  tests/test_reusable_workshop_journey.py tests/test_reusable_workshop_migration.py \
  tests/test_workshop_package_transfer_journey.py tests/test_workshop_redesign.py \
  tests/test_workshop_mcp_acceptance.py tests/test_mcp_manifest.py \
  tests/test_workshop_readiness.py tests/test_workshop_history_migration.py \
  tests/test_dataset_transfers.py tests/test_mcp_datasets.py tests/test_mcp_catalog.py \
  tests/test_mcp_resources.py tests/test_workshop_documents.py \
  tests/test_decision_workshop.py -q 2>&1 | tee /tmp/reusable-workshop-flow-regression.log
```

Observed: **155 passed, no skips, 81.52 seconds**. The 64 warnings concern the
test JWT signing key length. Real script runs covered 10,000 and 100,000 rows,
reproducibility, timeout, measured output/lineage and an explicit 10,000-row fork.
Other tests covered cross-source reuse, derivatives, empty output, later-step
failure, request conflicts, tenant isolation, package rejection, bindings,
trace changes/removals, migration and source document regressions.

The real fork used two retained Python scripts reading the same pinned source.
Both preview (50/50) and full publication (5,000/5,000) preserved explicit code-line
condition declarations and exact shared input cells. Declarative forks also round
tripped through MCP resources. Invalid forward/cyclic dependencies were rejected.

### Live MCP, installed CLI and local controller

```sh
set -o pipefail
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync python \
  tests/evidence/reusable_pipeline_replay.py 2>&1 | tee /tmp/reusable-workshop-live-mcp-flow.log
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync python \
  tests/evidence/workshop_connection_replay.py
```

Observed: **8 completed runs**. Same package reused for 10,000-row and two-row
sources, preview then publication; a declared two-script fork produced 5,000 rows
per branch; a paused binding ran manually, unchanged input was a no-op, and an
enabled ingestion binding published a changed source without a client run request.
Exact receipts and measured polling durations are in
`reusable-workshop-live-results.json`. The two-script fork completed within
10.14 seconds of polling; other client-observed durations ranged 2.05–14.88 seconds.
These include queue/poll latency and are not throughput benchmarks.

The harness creates a private fixture project; its bootstrap and teardown use the
local API container solely for fixture ownership. Workflow actions use MCP and
bytes use CLI. Finally, fixture keys are revoked, bindings paused and the project
deactivated. Receipts remain for inspection. The UI fixture below is retained.

Fresh saved-account MCP verification returned contract **5.1.0**, 67 scoped tools,
all ten current Workshop lifecycle tools, zero retired tools, successful resource
read and authenticated project discovery. From `/private/tmp`, without injected
credentials, the installed CLI command below returned `ready: true`, account-scoped
upload/export access and the same `http://localhost:8000` endpoint:

```sh
overmind connection check --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
```

### Frontend and transfer checks

From `frontend/`: `bun run typecheck`, `bun run lint`, `bun run check:all` and
`bun run test src/components/datasets/notebook/flow.test.ts src/components/datasets/notebook/diff.test.ts`
passed. Seven targeted tests passed; design, contrast in both themes and control
checks passed. Four isolated topology cases were retained because synthetic forks,
historical no-op fingerprints, ambiguous parent matches and cycles expose false
connections that ordinary linear browser data misses. They were written before
the topology implementation.

SDK dataset/connection/transfer regression: 38 passed; retained log
`/tmp/reusable-workshop-sdk.log`. The real CLI package upload/download round-trip
against REST is included in the 155-test suite.

### Failures found and fixed

- PostgreSQL rejected locking nullable joined relations; lock only the receipt row.
- Controller lacked the shared operation module mount; Compose now supplies it.
- Read-only container transfer and Docker log-driver differences required stdio
  transfer and retained bounded logs. Docker process exit establishes completion.
- Canvas initial focus raced layout; focus now uses resolved positions.
- Text-only ghost controls failed the existing guard; they use secondary controls.
- Run/header clutter and iteration selection obscured the unchanged cells; the
  obsolete bar is removed, and version inspection/restore lives in the name chip.

## Known boundaries

- Single-input acyclic script flows support forks. Multi-input joins and job-level
  conditional skipping are unsupported; all declared steps execute, including
  those producing zero rows. The last declared step is the selected output.
- Code-referenced conditions are agent declarations, not independent proof of
  semantic correctness. Receipt counts/fingerprints are measured separately.
- Bindings rebuild full snapshots. One controller handles one script run at a time;
  trace selections over 10,000 fail explicitly. This is not a distributed streaming engine.
- Pinned runtimes cannot install dependencies or access the network. Containers
  share a host kernel; no VM-level isolation claim is made.
- This run did not exhaustively inject host crashes, every OOM/provider interruption
  or long-duration multi-controller load. Unit/service coverage is not evidence of
  every operational failure mode. Light theme was checked statically, not visually.

Canvas screenshots, interaction evidence and the inline finish review are in
`workshop-flow-ui.md`.

## Final verification

After documentation/resource guidance changes, the MCP catalogue, manifest and
resource tests passed again: 34 passed in 2.28 seconds; command
`UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_mcp_resources.py tests/test_mcp_manifest.py tests/test_mcp_catalog.py -q`,
log `/tmp/workshop-flow-contract-final.log`. Frontend typecheck, lint, all design
guards and the seven targeted tests passed again. Targeted pre-commit checks
passed after applying Markdown formatting; log `/tmp/reusable-workshop-precommit-final.log`.
Generated-client whitespace normalization follows the existing generation target;
no generated API semantics were hand-edited. The controller was running and no
containers with `overmind.workshop.run` labels remained. A fresh browser reload
showed version 1.2 with no captured warnings/errors; viewport override was reset
and the temporary verification tab closed. No commits or pushes were made.
