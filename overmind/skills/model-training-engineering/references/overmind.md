# Overmind execution reference

Read this when applying model-training-engineering to Overmind. Discover current
schemas from the connected catalogue/native prompts; do not copy obsolete API calls.
Use installed overmind-datasets, overmind-training and their model-workflows reference
for detailed contracts. This reference covers cross-stage failure boundaries.

## Resolve and preserve

- Call list_projects and pass project_id explicitly. Match the returned endpoint to
  the intended deployment before writes. Local and hosted project IDs/credentials
  are not interchangeable. Check the CLI connection independently for local bytes.
  Missing credentials/tools never authorize Console automation or training as a connectivity test.
- Record source dataset/cell/fingerprint, pipeline revision/package, partition members,
  plan, preparation/setup, experiment, provider call and comparison IDs.
- Inspect old job/provider state before cancelling or restarting. Preparation may be
  shared. A displayed setup record and training row do not prove two provider jobs.
  Inspect existing receipts after unknown responses; preserve the same request key.

## Data handoff

- Upload approved original bytes with the installed CLI, retain its transfer receipt,
  then verify completed landing with inspect_dataset/query_dataset. Publication
  acknowledgement is not completed landing.
- Audit source fingerprints as well as counts. Distinct raw records sharing a field
  such as state must not collapse merely because that field resembles a model input.
  Raw-record hashes, native-input hashes and explicit entity groups serve different purposes.
- For confirmed corrupted immutable lineage, rebuild the approved original bytes into
  an explicitly attributed replacement source. Preserve old artifacts; never strip
  inherited lineage, rename fields to conceal the defect or append duplicate sources.
- Reuse a compatible retained pipeline revision. Inspect its flow/package before
  adapting; validate, preview, inspect coverage/checks, publish once with a stable key.
  Use explicit source_cell and source_fingerprint. Confirm runner readiness.
- Create native train/development/calibration/final partitions where appropriate.
  Check actual memberships and inherited/declared groups, not only split fractions.
  Training and evaluation member projections have different target envelopes.
- Save source-bound training plans and superseding prepared/partition snapshots.
  Audit, baseline and sanity reports need methods, scope and limitations. Static
  encoding checks must not be described as successful neural fitting.

## Exact training and comparison lifecycle

- Discover supported models, methods, batch bounds and runtimes. Resolve qualified
  unchanged-base and alternate decision identities; a provider alias is not verified weights.
- Carry the same explicit model, training type, context length, train/development
  cells and monitoring through preparation, readiness and launch.
  In particular, omitted context after explicit preparation may resolve to a different
  setup. Check requested/effective settings and promote the matching setup.
- Use prepare_training_data for model-specific tokenization and inspect its exact
  report. It is distinct from prepare_training_experiment, which verifies/forecasts.
- For native comparisons, create a draft native evaluation with frozen calibration/
  final cells and base/alternate participants. Bind it to the training experiment;
  launch schedules the trained candidate comparison. Do not independently dispatch
  the same draft as duplicate work.
- Use a plan-bound full experiment for requested full training; no pilot is required.
  Optional pilot selection needs matching task/data/development evidence. A bounded
  hardware profile is not quality evidence.
- Freeze pre-training baseline, development validation, monitoring, checkpoint selection
  and failure policy. Required checks should fail closed. The initial baseline is
  measured before optimizer steps, not a final benchmark.
- Launch once with the saved identity and required platform preparation receipt. Read the resulting job, matching setup
  promotion, effective settings, provider acknowledgement and measured progress.
  Use inspect_training_progress for retained checks/examples; reads do not run inference.

## Recovery and completion

Control-plane collection and provider execution are separate. Recover retained
provider calls and sealed evidence before considering another submission. Unknown
acknowledgements stay unknown; unsupported replay risks duplicate work.
Completed_with_issues requires per-arm coverage review. Failed checkpoints stop
dependent comparisons; missing external outputs are not zero scores.

Retained adapter reload checks do not establish full optimizer resume. Native decision
artifacts do not become chat deployments automatically. Training completion does not
authorize activation. When testing local fixes, distinguish API/hot-reloaded workers
from long-lived isolated-runner controllers and verify the actual new path.
