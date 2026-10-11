# Conditional transformation lifecycle verification

## Scope and failures to exercise before implementation

MCP-ready: discover, retrieve exact revisions and their family history, author
conditions, validate, preview, execute, revise, derive variants and inspect outputs.
CLI-guided: exact retained package upload/download. No paid model services.

The live experiment must author if/elif/else and nested conditions, retrieve code
through a fresh MCP session, derive a changed-column variant from retrieved bytes,
change parameters without changing a revision, and revise without changing history.
Verify exact branch members, not only counts, at boundaries, with nulls, Unicode,
duplicates, no matches and 10,000/100,000 rows. Invalid graphs, stale revisions,
changed request keys and downstream exceptions must preserve prior usable cells.
Declarations are not proof of exclusive/exhaustive branches; scripts own routing.

Potential agent friction: no exact-revision tool lookup, missing workflow prompt,
retrieval truncation or dropped provenance, confusing family/revision identity,
contradictory installed guidance, and discovering limitations only after execution.
Any deterministic replay demonstrates this agent-authored workflow, not a measured
success rate for all third-party coding agents. No secondary agent is being launched.

## Full-size downward canvas acceptance

The user's correction supersedes the earlier fit-to-view design. Preserve
NotebookCell byte-for-byte. Match the original max-w-7xl notebook container and
40px left / 8px mobile or 16px desktop right padding. Cells render at scale 1;
initial positioning, reset, resize and pinch gestures must not shrink them.
The user's supplied branching reference supersedes the single-column interpretation:
progress runs downward, with top inputs and bottom outputs. Sibling branches align
side by side on the same horizontal layer. Descendants occupy the layer beneath
their parent. Every cell remains full-width and unscaled; pan to other branches.
Ignore the old horizontal layout's saved positions by using a direction-specific
layout key. Grid movement and vertical panning remain available. Verify desktop,
mobile, saved positions, dynamic cell heights and untouched cell content.

## Changes and MCP impact

- MCP-ready: contract 5.2.0; `author-dataset-transformation` prompt;
  `inspect_dataset_workbench(pipeline=REVISION_UUID)` returns the exact recipe,
  paged family history and revision-scoped runs/bindings. Other project revisions
  cannot be retrieved through the selected project.
- Distinct `revision_conflict` replaces a generic dataset error for stale revision
  writes. Server instructions no longer contradict the isolated script runner.
- CLI-guided: retained package bytes still use upload/download. MCP resources
  provide complete code through bounded, checksum-verified pagination.
- UI-only: unchanged NotebookCell contents; original responsive notebook width,
  scale 1, top/bottom handles, downward ELK layers, aligned siblings, grid movement.
  No auto-fit. Resizing preserves the focused cell's position rather than leaving
  it off-screen. Dataset changes reset the canvas's scope and saved layout key.

## Repeatable live experiment

Requirements: local Compose API/controller, approved Workshop runtime image,
running Docker daemon, repository Python dependencies and in-repo CLI package.
The script creates a private synthetic project/account through the API container
solely for fixture ownership. Every workflow action then uses real Streamable HTTP
MCP; file bytes use the CLI from a repository-free temporary directory. No paid
training, evaluation or inference services are called.

```sh
set -o pipefail
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync python \
  tests/evidence/conditional_pipeline_replay.py \
  2>&1 | tee /tmp/conditional-workshop-live.log
```

Observed results: six successful runs and one expected downstream failure, all
seven with the expected outcome. Exact receipts and durations are in
`conditional-workshop-live-results.json`.

| Experiment                                                                          | Per-step rows                      | Observed seconds |
| ----------------------------------------------------------------------------------- | ---------------------------------- | ---------------- |
| Preview of first 100 rows                                                           | 100 / 40 / 10 / 60                 | 12.238           |
| Null, boolean/type, threshold, nested priority, Unicode and duplicate-content cases | 10,000 / 4,000 / 1,000 / 6,000     | 18.243           |
| Parameter reuse, no accepted rows                                                   | 10,000 / 0 / 0 / 10,000            | 16.222           |
| Retrieved-code adaptation to renamed source field, reordered branches               | 100 / 60 / 40 / 10                 | 12.209           |
| New revision with strict threshold                                                  | 10,000 / 2,000 / 1,000 / 8,000     | 20.264           |
| Original revision reused after revision update                                      | 100,000 / 40,000 / 10,000 / 60,000 | 56.662           |
| Exception in third step                                                             | 10,000 / 4,000 / failed            | 12.152           |

Each published output was exported and checked against independent exact member
sets, not only counts. Unicode and complete nested probability targets were
checked in every output row. MCP SQL counts independently agreed with the exports.
Accepted/fallback coverage and disjointness were measured by the test, not inferred
by the platform. Repeated identical run keys returned the same receipt.

A fresh MCP client retrieved the original revision and all package files in
173-character pages; SHA-256 and downloaded ZIP contents matched the authored
files. The agent-authored variant used those retrieved bytes and retained
`derived_from`; the original recipe remained identical. Wrong-source contracts,
stale revisions, duplicate IDs, forward references, unknown parents, multi-input
joins and invalid condition line references were rejected. Failed execution
published no cells and left the active cell unchanged. Fixture credentials were
revoked and private test projects deactivated; receipts remain retained.

### Failures found, corrected and retested

- The initial new contract tests failed for missing exact-revision lookup and
  the absent prompt. After implementation, the stale-revision test exposed a
  generic error code; the distinct conflict code fixed it. 34 targeted tests passed.
- Two harness mistakes were corrected: a helper argument collided with recipe
  `name`, and dataset-scoped export does not accept `--project-id`. The final
  complete real-MCP run passed after both corrections; these were harness defects.
- UI checks rejected sideways flow and fit-to-view shrinking. The single-column
  interpretation was then superseded by the user's explicit layered reference.
- Layered mobile reflow initially moved the focused cell outside the viewport.
  Tracking its layout anchor fixed the resize; desktop-to-mobile and cell-outline
  navigation were retested at scale 1.

## Regression commands and results

```sh
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync pytest \
  tests/test_reusable_workshop_journey.py tests/test_reusable_workshop_migration.py \
  tests/test_workshop_package_transfer_journey.py tests/test_workshop_redesign.py \
  tests/test_workshop_mcp_acceptance.py tests/test_mcp_manifest.py \
  tests/test_workshop_readiness.py tests/test_workshop_history_migration.py \
  tests/test_dataset_transfers.py tests/test_mcp_datasets.py tests/test_mcp_catalog.py \
  tests/test_mcp_resources.py tests/test_workshop_documents.py \
  tests/test_decision_workshop.py tests/test_mcp_prompts.py -q \
  2>&1 | tee /tmp/conditional-workshop-regression.log

WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea \
WORKSHOP_DOCKER_SOCKET=/Users/tyleredwards/.docker/run/docker.sock \
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync pytest \
  tests/test_reusable_workshop_journey.py \
  -k 'script_branch_declarations or real_isolated_script' -q \
  2>&1 | tee /tmp/conditional-workshop-docker.log
```

181 passed in the first command; its three Docker-gated cases subsequently passed
in the second command, including 10,000/100,000-row isolation and timeouts.
64 warnings concern short test-only JWT secrets; they are not new runtime failures.
Frontend typecheck and lint passed; four recorded-lineage tests passed. The
attempted additional `versions.test.tsx` filter matched no file and is not coverage.
Targeted pre-commit checks passed, including Python, frontend and documentation.

## Browser evidence and inline finish review

The existing synthetic fixture and reproduction command are in `workshop-flow-ui.md`.
Its earlier Fit View approval is superseded by this user's full-size/layered brief.
No user datasets were mutated. Browser review used the existing dark theme.

Captured and visually inspected:

- `.impeccable/review/workshop-layered-desktop.jpg`: 1440×900.
- `.impeccable/review/workshop-layered-mobile.jpg`: 390×844.
- `.impeccable/review/workshop-layered-user-1280.jpg`: 1280×720, recaptured after
  an initial malformed capture; only the corrected capture is review evidence.

Measured desktop widths were 1,224px at 1440, 1,158px at 1280 and 324px at 390,
matching the original max-w-7xl container and padding. Every measured viewport
transform retained scale(1). Siblings had identical y=380 world positions, also
after one script panel expanded from 294px to 408px. Source was above both.
An existing two-cell dataset displayed a downward aligned chain at full width.
Outline navigation brought the off-screen branch fully into view. Wheel panning,
reset, pointer-grid movement, keyboard-grid movement and reload persistence worked.

disposition: ship

Review performed inline under Impeccable's finish-review instructions, not by a
subagent. This is a user-pinned ordinary extension: no replacement-world concept
roll or quality card applies. The supplied branching screenshot is the topology
reference; the existing NotebookCell is the visual authority for cell interiors.

### persistence

Pass: PRODUCT.md, DESIGN.md and frontend/Workshop guidance record full-size cells,
downward stages and horizontal sibling layers. All three final captures are valid.

### fidelity

| Element        | Finding                                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------------------------- |
| TYPE           | Match: existing cell typography, labels and text size preserved.                                              |
| MATERIAL       | Match: existing flat token surfaces and borders, no new decoration.                                           |
| GROUND         | Match: unchanged warm-black Console/card tokens.                                                              |
| Cell internals | Match: no NotebookCell edits in this correction.                                                              |
| Progression    | Match: parent above siblings aligned on one horizontal layer.                                                 |
| Scale          | Match: original notebook widths and 100% scale; no automatic shrinking.                                       |
| Wide branches  | Adaptation: pan or use the outline to reach full-width siblings, required by the user's full-size constraint. |
| Interaction    | Match: retained elbows, grid movement, version chip and cell controls.                                        |

### ceiling

Reached for the pinned layout correction. Replacing cells with compact node cards
or shrinking all branches into one viewport would contradict the user's constraint.

### material_fixes

None remaining in the reviewed correction. The malformed capture was replaced;
the resize-anchor failure was fixed and retested before this review.

### keep

Preserve cell internals and measured lineage; distinguish declared conditions from
verified outcomes. Siblings share a layer; later stages progress downward.

## Inline documentation pass

Updated the Workshop paragraph in DESIGN.md; retained its schema and all tokens.
Palette: unchanged semantic warm-neutral colors.
Type: unchanged Console and cell hierarchy.
Depth: existing flat surfaces and border treatment.
Geometry: original cell widths; 20px movement grid and horizontal branch layers.
Named rule: no auto-fit shrinking of cell contents.
Unrelated typography wording and light-theme visual review were not expanded into
this correction. No design-system regeneration or new sidecar primitives were needed.

## Capability boundaries and unmeasured coverage

The native coding agent authored these scripts and the catalogue-driven replay;
fresh-client retrieval proves retained contracts/bytes, not a broad success rate
across third-party agent models. No secondary agents were launched.
Single-input acyclic row-routing remains supported; multi-input joins, cycles,
job-level conditional skipping and automatic exclusivity/exhaustiveness proofs
remain unsupported and explicitly discoverable. The test covered null scores,
not every absent nested-field combination. Existing conflict/security/lineage
regressions supplement the live cases; the experiment is not exhaustive fuzzing.

The 100,000-row four-step run took 56.7 seconds including queue/polling latency,
not a general throughput guarantee. Preview still has approximately 12 seconds
of controller/container overhead in this local environment. One controller handles
one script run at a time; this run did not test concurrent tenants, long-duration
load, every host crash/OOM or provider interruption. No paid providers were invoked.

## Final readiness checks

`uv run --no-sync python tests/evidence/workshop_connection_replay.py` using the
saved local account connection returned server 5.2.0, 67 scoped tools, all ten
Workshop lifecycle tools, no retired tools, successful resource retrieval and
authenticated project discovery. This is a fresh MCP SDK connection, not a claim
that already-open third-party clients have refreshed their cached tool schemas.

Frontend typecheck, lint and all design/contrast/control guards passed after the
final changes. Both bundled Overmind skills passed the skill-creator validator.
Targeted pre-commit checks passed. A final browser reload showed scale 1 and equal
sibling positions with no new captured browser errors or warnings. Verification
used MCP for platform workflows and the browser only for the explicitly requested
UI correction. No commits or pushes were made.
