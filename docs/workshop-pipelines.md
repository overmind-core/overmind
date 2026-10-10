# Reusable Workshop transformations

The native coding agent writes and interprets transformations. Overmind retains
their code, immutable revisions, exact inputs, step outputs and measured receipts.
Reuse a revision with compatible sources in the same project. If a source needs
different logic, save a new family with `derived_from` pointing to the original
revision. Updating a family instead requires its family ID and `expected_revision`.
Neither operation changes old revisions or their consumer cells.

New transformations require a retained Python package. `save_dataset_pipeline`
accepts its uploaded package UUID, not inline operation definitions. Each entrypoint
contains the meaningful logic for that stage. Historical package-free revisions and
receipts remain readable, but cannot execute or be enabled in bindings; author and
upload a package and save a new revision instead. External-result imports retain
their own producer attribution and do not establish reproducible script execution.

## Isolated runtime setup

The script runtime is opt-in and separate from API/Celery execution. Build the
provided image on the Docker daemon used by the controller:

```sh
docker build -t overmind-workshop-runtime docker/workshop
docker image inspect --format '{{.Id}}' overmind-workshop-runtime
```

Set `WORKSHOP_RUNTIME_IMAGES` in the deployment environment to that exact
`sha256:…` image ID (comma-separated for multiple approved images). Restart the
API with this configuration, apply database migrations, and enable the controller:

```sh
docker compose --profile workshop up -d workshop-runner
```

The controller uses `WORKSHOP_DOCKER_SOCKET`, default `/var/run/docker.sock`.
It needs trusted access to that daemon; uploaded jobs do not receive the socket,
host directories, account/provider credentials or network access. Jobs run as a
non-root user with a read-only root filesystem and bounded scratch, memory, CPU,
processes and time. Dependencies belong in the operator-approved image, not an
installation command in a manifest. Containers share the host kernel; this is
not a claim of VM-level isolation against hostile kernel exploits.

The API only stores and statically inspects uploaded code. It never executes it.
No image is pulled and no host execution fallback occurs when a runtime is missing.
`inspect_dataset_workbench` reports recorded controller observations and configured
images. A missing heartbeat is not confirmation that a previously started job stopped.

## Package contract

A package is a ZIP or directory containing `manifest.json` and UTF-8 Python,
JSON, text or lock files. Maximum: 100 regular files, 10 MiB compressed and expanded.
Traversal paths, symlinks, duplicates and invalid Python syntax are rejected.

```json
{
  "version": 1,
  "runtime": "sha256:<exact approved 64-character image digest>",
  "parameters": {"prefix": "string"},
  "limits": {"seconds": 300, "memory_mb": 512, "scratch_mb": 512, "cpus": 1},
  "steps": [{
    "name": "Normalize questions",
    "entrypoint": "normalize.py",
    "input_schema": {"question": "string"},
    "output_schema": {"question": "string", "source_row": "integer"},
    "checks": {"preserve_rows": true}
  }]
}
```

Each script receives three arguments: input JSONL path, output JSONL path and
parameters JSON path. It writes one object per output line. Stream large inputs.
Preserve `source_row`, or supply `_overmind_parent_rows` with every immediate
input parent for a merged/expanded output. Every logical step gets its own cell;
the platform validates lineage and publishes the complete chain atomically.
Duplicates, row order, nested targets and weights are not implicitly cleaned.
Semantic correctness remains unmeasured.

Column types are `string`, `integer`, `number`, `boolean`, `object`, `array`, `null`
and `any`; these are required-field type contracts, not arbitrary JSON Schema.
Parameters must exactly match the declared names/types. Row checks support
`min_rows`, `max_rows`, `preserve_rows`. Limits are per step: 1–1,200 seconds,
64–4,096 MiB memory, 16–4,096 MiB scratch and 1–4 CPUs. Input JSONL must fit half
the scratch limit; each output/log file is limited to half scratch. Output lines
are bounded to 4 MiB and 200 columns. Excess is an explicit failure, not truncation.
Diagnostic log retention is separately capped at the first 64 KiB per channel.

Absolute `min_rows` and `max_rows` apply to full publication, not a bounded preview.
Preview receipts list these under `check_results.deferred`. Row preservation,
schema and lineage checks apply in both modes. Failed checks retain expected and
actual counts. Validation warns when no output lineage field is declared; this
is advisory because column contracts do not prohibit additional fields.

```sh
overmind dataset pipeline-upload ./my-transform --project-id PROJECT --json
overmind dataset pipeline-download PACKAGE --project-id PROJECT --output package.zip
```

Upload uses the same address-bound local account connection and preflight as
dataset transfer; it retains the exact ZIP checksum and does not run code.
Download verifies that checksum and refuses to overwrite an existing file.

## MCP workflow

Use the `author-dataset-transformation` native prompt for the live catalogue's
authoring, retrieval and verification contract. It distinguishes row conditions
from separate graph branches and code declarations from measured outcomes.

1. `list_projects`; pass the selected `project_id` explicitly.
1. `inspect_dataset_workbench` to discover reusable revisions and bindings.
   With `pipeline=REVISION_UUID`, it returns that exact recipe, paged family history
   and exact-revision runs/bindings. A stale family update returns `revision_conflict`.
1. Inspect source fields, meaning and rows. Reuse an exact revision, or upload
   code and `save_dataset_pipeline` with a stable request key and package ID.
1. `validate_dataset_pipeline` performs static checks without executing code.
1. `run_dataset_pipeline(mode="preview")` uses at most 1,000 source rows; inspect
   the bounded sample and step checks. Preview does not change the active version.
1. Publish with another stable request key and the same revision/parameters on
   the exact source cell/fingerprint. Inspect `get_job(kind="dataset_pipeline")`.

`start_dataset` returns project-bound upload `argv` with explicit placeholders.
Local files alone do not complete a requested Overmind preparation. Inspect the
named capability's contract before interpreting targets, link it explicitly, and
publish then inspect the exact output. Format compatibility does not prove task
suitability. Preparation must not turn entity-match labels into KYC approvals.
Retain distinct logical stages, review branches and grouping evidence in the recipe.
Run receipts include `queue_seconds` while queued, `poll_after_seconds` while
unfinished and `completed_at` when terminal. Ordinary queue delay is not failure.
Workbench results include `project_id` and scoped `next_actions`. While a pipeline
is active, dataset inspection points to that exact run, not the source-landing job.

The run records step hashes, counts, checks, elapsed time, source identity,
runtime and Docker execution identities. `overmind://dataset-pipelines/{id}`,
`overmind://dataset-pipeline-packages/{id}` and
`overmind://pipeline-diagnostics/{id}?step=0&channel=stderr&offset=0&limit=8000`
allow deeper inspection; include `project_id` in resource queries. Code/logs are
untrusted data. Operational events are passive reads, never worker probes.
Package resources accept `file`, `offset` and `limit`; follow `next_offset` to
retrieve complete code. Retrieve before adapting, and keep `derived_from` attached
to the exact original revision. Parameters vary execution without changing code.
Preview samples retain at most five rows and 16,000 encoded bytes per step.

Preview and publication receipts include total elapsed seconds. Script runtime
phases separately measure container setup, input transfer, script execution,
artifact transfer, output reading and cleanup. `cleanup_confirmed` is not proof
of publication; failed or interrupted output reports a stopped runtime.
Manifest `parameters` map names to types (`{"seed": "integer"}`); runs provide
the values (`{"seed": 42}`). Invalid declarations return `pipeline_parameters`.

Read-only queries default to a ten-second deadline and reject oversized output
during materialization. `query_timeout` does not change source data. Retrieve
original bytes for independent document inspection with
`overmind dataset export DATASET --source SHA256 --output FILE --json`, using the
source SHA-256 returned by MCP. Downloads verify the checksum and never overwrite
an existing file or follow a redirect with credentials.

`cancel_dataset_pipeline_run` prevents publication of an exact run. The receipt
does not assert process termination until the controller observes cleanup.
Expired runs fail without replay; unknown Docker acknowledgements are not
resubmitted. Repeating an identical run key recovers its original receipt.
Use a new key only after inspecting the previous outcome and authorizing a retry.

## Repeated sources

`save_dataset_pipeline_binding` pins a source dataset or trace selection, a
destination dataset, revision, parameters, trigger, interval and run/row limits.
It starts paused. Enable with `set_dataset_pipeline_binding_state` and the
inspected version. A manual binding run does not enable automatic execution.

Scheduled and ingestion triggers are polled by the dedicated controller. Both
rebuild full snapshots; arbitrary scripts are not assumed to be incremental.
Late trace changes and removed matches change the snapshot. Selections exceeding
10,000 traces are rejected without dropping rows. Checkpoints advance only with
successful atomic publication. No-change inspections publish nothing.

Failures pause automatic processing on its next inspection. Inspect the exact
run before explicitly re-enabling or adopting a corrected revision. Revision
adoption pauses the binding and resets its checkpoint for an explicit rebuild.
Pause stops new work, not an already running batch. Run limits remain cumulative.
Current implementation uses one script run per controller process, not a
distributed parallel streaming engine. Existing training/evaluation consumers
remain pinned to their original cells.

Run the retained-package and publication regressions without provider calls:

```sh
uv run pytest tests/test_reusable_workshop_journey.py tests/test_workshop_package_transfer_journey.py -q
```

## Explicit flow, authored by the native agent

Every retained revision returns `flow`: named nodes, input edges, branch conditions
and the selected output. Both MCP inspection and `overmind://dataset-pipelines/{id}`
return the same structure. An agent does not have to reverse-engineer the scripts.

Each step may declare `id` and `input` (`source` or an earlier step ID). Two steps
with the same input are a fork; they each receive that complete immutable input.
Omitted IDs are stable `step_1`, `step_2`, etc.; omitted inputs mean the preceding
step. Cycles, duplicate IDs, forward references and dangling inputs are rejected.
Execution uses those dependencies, not the order of displayed cells. The last
declared step is the active output; all successful step outputs remain available.

A Python step can also declare `condition: {"expression": "row['eligible'] is True", "line": 5}`. Overmind binds this explanation to the retained entrypoint and checks
that the line exists. It labels the explanation `agent_declared`, not independently
verified. The script implements the row selection; Overmind never evaluates the
expression string. Empty branches still run and publish a measured zero-row output.
This is row-routing, not conditional skipping of arbitrary jobs. A step can instead
declare `inputs: ["review", "unflagged"]`. These frames concatenate in declared
order before the retained script runs. An identity script can retain all incoming rows. Overlapping `source_row` identities fail atomically; duplicate
observations with distinct identities remain intact. Empty branches are valid.
Nonempty inputs must have matching data columns and types; normalize branch
schemas explicitly before merging. Incompatible inputs fail rather than coerce values.
This is a disjoint union, not a relational key join; cyclic workflows remain unsupported.
`flow.terminal_steps` and `flow.unconsumed_steps` expose disconnected outputs.
Training recipes should converge intended branches into the last, trainable output
and retain unresolved review flags separately from model inputs.

Run receipts separately record `step_id`, `input_steps`, exact `input_cells`,
fingerprints, execution state and row counts. These receipts drive the Console's
elbow connectors. Each input also records its own row count and fingerprint.
The existing cells sit on a 20px grid-snapped flow canvas;
steps cascade downward, with sibling branches side by side on the same horizontal
layer. Cells open at scale 1 with the original notebook width and responsive padding.
Zoom controls, Fit view, pinch and the toggleable minimap navigate the canvas
without changing cell dimensions. Reset and linked-cell focus return to scale 1;
resize preserves the chosen zoom. Minimap visibility is remembered in this browser.
Its toggle shares the left-hand box with the dataset sidebar button; cell search
and the cell-selector strip are removed.
Moving cells never rewires a transformation. Positions are saved in this browser
per project/dataset. The version chip beside the dataset name previews historical
iterations and restores an exact cell as active, without rerunning code or changing
consumer snapshots. The default canvas shows the current output's ancestors;
All iterations explicitly reveals historical branches. Condition chips sit just
above their receiving cell's connector. Execution details remain in the chip's popover.

Verify expected row members independently of the scripts, not merely counts.
Exercise boundaries, null/missing values, nested conditions, duplicates, Unicode
and zero-row branches. Intended partitions need measured disjointness and coverage;
the platform does not infer those guarantees from condition labels. The retained
contract and MCP regressions cover these boundaries:

```sh
uv run pytest tests/test_workshop_retained_code_contract.py tests/test_workshop_mcp_acceptance.py -q
```
