# Autonomous training-data preparation: source review and implementation

Reviewed 6 November 2026. User input is data plus “I want to train a decision
model on this data”. No target, cleaning plan, feature list or split strategy is
supplied to the agent. The harness supplies generic tool contracts and engineering
practice; dataset-specific interpretation belongs to the agent. Absence of user
instructions does not remove system instructions, schemas or execution limits.

## Source scope

Read source files at these revisions, without installing or executing the projects:

| Project                   | Revision                                   | Files inspected                                                                                                         |
| ------------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| Microsoft Data Formulator | `5477f0e236426dc8f74a498ec400414fba7fbc0f` | `agents/context.py`, `agents/agent_workflow_distill.py`, `data_operations/executor.py` under `py-src/data_formulator`   |
| Data-Juicer               | `65383e6f8f0dc0b3c74de8475b0f8abdab700f51` | `data_juicer/core/analyzer.py`, `preflight.py`, `executor/default_executor.py`                                          |
| marimo                    | `1574a2347603b0ad8c76d38743dd78e4de600ae0` | `marimo/_runtime/dataflow/graph.py`, `_ai/_tools/tools/tables_and_variables.py`, `errors.py`                            |
| Data Prep Kit             | `bbe6608bb1fae641ea1c2c5c62ee2c6b6a92ffbe` | `data-processing-lib/python/src/data_processing/transform/{abstract_transform,table_transform,transform_statistics}.py` |

This is a focused architectural review, not a whole-repository security audit or
comparative performance benchmark. No upstream implementation was copied.

## Data Formulator: context and exploration history

[Context source](https://github.com/microsoft/data-formulator/blob/5477f0e236426dc8f74a498ec400414fba7fbc0f/py-src/data_formulator/agents/context.py)
constructs detailed context for the focused thread and compact descriptions of
other threads. Table context combines schema, descriptions, provenance, bounded
field values and row samples. Inspection has explicit sample budgets and reports
errors without destroying all table context.

Adopt bounded, source-bound observations and retained exploration decisions.
Context should explain what exists and what was measured, with receipts for full
results. Do not repeatedly dump the entire project recipe catalogue into each turn.
Our existing conversation window and receipt retrieval cover part of this already.

[Workflow distillation](https://github.com/microsoft/data-formulator/blob/5477f0e236426dc8f74a498ec400414fba7fbc0f/py-src/data_formulator/agents/agent_workflow_distill.py)
extracts abstract and concrete reusable workflows from chronological events. It
explicitly removes abandoned attempts and corrective exchanges from the reusable
recipe. Overmind should retain those exchanges as audit evidence while publishing
only the successful executable recipe. A model-written summary cannot establish
that a recipe replays: clean-process execution must establish that separately.

[The connector executor](https://github.com/microsoft/data-formulator/blob/5477f0e236426dc8f74a498ec400414fba7fbc0f/py-src/data_formulator/data_operations/executor.py)
finds previously published results by operation ID and plan hash, skips completed
steps, and returns partial failures. This supports the same distinction as our
request keys: reusing a known result differs from rerunning an uncertain action.
Keep Overmind's existing Temporal receipts and atomic dataset publication.

## Data-Juicer: measure, refine, execute

[Analyzer](https://github.com/datajuicer/data-juicer/blob/65383e6f8f0dc0b3c74de8475b0f8abdab700f51/data_juicer/core/analyzer.py)
collects filter statistics without applying the filtering operation, and runs
annotation operators. Automatic analysis can use a bounded subset. This is useful:
measure the proposed cleaning rule's effect before dropping or changing rows.
A sample finding must retain its coverage and must not become a full-source claim.

[Preflight](https://github.com/datajuicer/data-juicer/blob/65383e6f8f0dc0b3c74de8475b0f8abdab700f51/data_juicer/core/preflight.py)
checks operator names, parameters and types before instantiation. The default
executor then checks instantiated operators against schema/environment and can
resume from dataset checkpoints. These checks catch mechanical mistakes, not
whether a selected label or cleaning rule serves the user's intended prediction.

Adopt capability checks before execution and per-transform measurements. Avoid
bringing its full executor, operator dependencies or model downloads into the POC.
Curated operators can later run behind our existing package interface. Its
[Sandbox](https://datajuicer.github.io/data-juicer/en/v1.4.4/docs/Sandbox.html)
probe/refine/train/evaluate cycle is a useful later optimisation pattern; it is not
an OS isolation boundary and does not replace Daytona or authorise training runs.

## marimo: observable state and invalidation

[DirectedGraph](https://github.com/marimo-team/marimo/blob/1574a2347603b0ad8c76d38743dd78e4de600ae0/marimo/_runtime/dataflow/graph.py)
tracks definitions, references, cycles, ancestors and stale descendants. The AI
tools expose current table metadata, variable values and cell errors. The important
pattern is that the agent can inspect execution state instead of inferring it from
its own narrative or remembered commands.

Adopt explicit experiment inputs, script and parameter hashes, output hashes,
errors and downstream invalidation. Prefer marking expensive results stale to
automatically rerunning them. Overmind already owns the published pipeline graph;
introducing a second notebook server and graph would create competing authorities.
Start with file-based experiments in Daytona and immutable publication receipts.
A persistent filesystem need not mean a persistent Python kernel with hidden state.

## Data Prep Kit: transform boundary and measurements

[AbstractTableTransform](https://github.com/data-prep-kit/data-prep-kit/blob/bbe6608bb1fae641ea1c2c5c62ee2c6b6a92ffbe/data-processing-lib/python/src/data_processing/transform/table_transform.py)
accepts an Arrow table and returns output tables plus statistics. Its binary
adapter handles serialisation and a flush hook handles buffered transforms.
This separation is useful for shared deterministic transforms across runtimes.
Some adapter failures return empty outputs and error counters; Overmind must turn
those into explicit failures rather than accept an empty training set as success.
Statistics aggregation is not a semantic validator. Distinguish counts, rates,
unknown values and per-source evidence rather than summing arbitrary metrics.

Keep our existing JSONL entrypoint contract for publication. Add selective
Arrow-backed operators only when a real workload needs them; do not install the
entire framework for a tabular POC.

## Overmind design

1. **Discover:** inspect source metadata, actual values and relevant project context.
   Generate candidate target meanings and prediction units. Choose a supported
   interpretation or continue investigating alternatives; never use a column-name
   heuristic as proof. With genuinely indistinguishable tasks, expose the unresolved
   choice rather than inventing supervision.
1. **Assess:** the agent records hypotheses, supported conclusions, assumptions and
   unresolved questions about targets, features, splits, supervision and quality.
   Evidence links point to prior task actions. This survives context compaction.
1. **Experiment:** profile, write Python/SQL, execute, read output, revise. Experiments
   declare source versions, code, parameters and checks. Checks may contradict the
   assessment, which the agent must revise. Retain failed experiments for debugging.
1. **Prepare:** fit preprocessing on training data only where fitting is needed.
   Preserve original identity and supervision semantics. Freeze parameters, source
   identities and package versions. Do not require unnecessary scaling/encoding just
   because a generic ML checklist includes it.
1. **Publish:** replay the retained recipe in a clean process, inspect previews,
   publish through existing dataset services, then verify actual output. Record
   compatibility separately from evidence about task suitability.

Pydantic AI owns the reasoning loop. Temporal owns durable action ordering,
waiting and reconciliation. Daytona owns the private persistent workspace and
prebuilt tool environment. The platform owns tenancy, lineage, immutable dataset
versions and consumers. Hosted inference remains the model endpoint; no new Modal
serving deployment is required for this work.

Use a locked image with tabular, statistical, spreadsheet and text libraries
installed at build time. Verify readiness before reasoning begins. No runtime
package installation. Heavy optional modality dependencies belong in separately
qualified images, not every project workspace. Retained outputs must outlive a
workspace; checkpoint hashes and publication receipts are recovery boundaries.

Start with one active task per project workspace to avoid concurrent filesystem
mutation. Scale across project workspaces, with admission limits and bounded command
resources. Large data needs streaming or external batch execution; loading every
source into pandas is not a scaling strategy. Never blindly retry an unknown remote
command. A lost model response is safer to retry only before a side effect is issued.

## Implementation status and acceptance

Implemented: agent-authored structured training assessment, receipt-reference
validation, durable context inclusion, generic minimal-intent instructions, runtime
bootstrap and a deployed, qualified Daytona image. Exploration and publication
share locked libraries and offline assets. The active workspace has 4 CPUs, 8 GiB
RAM and 10 GiB disk; the previous workspace is stopped and retained for rollback.
Bootstrap exposes the full consumer contracts. Actionable tool errors support
correction, and repeated identical invalid calls block before another model call.

Task-wide token budgets have been removed. Usage and reported cost remain measured;
action-count limits are opt-in. An uncapped minimal-prompt Titanic trial blocked
without publishing trainable output; see the [measured review](native-workshop-runtime-trial.md).
Earlier capped trials did not publish prepared output and cannot establish whether
the model would finish without those limits.

Still required: experiment manifests and stale-output checks, completed autonomous
clean-replay qualification, and broader dataset trials. Assessment validation proves
that references exist, not that the interpretation is true. Runtime qualification
and passing platform tests do not establish autonomous preparation quality.

Qualification prompt, unchanged for every dataset: “I want to train a decision
model on this data”. Do not embed Titanic-specific labels, leakage columns or
expected transformations in the agent prompt. The evaluator can know those facts.
Use Titanic plus unfamiliar variants: renamed columns with metadata, additional
leakage proxies, repeated entities, missing labels, already prepared soft targets,
and an intentionally ambiguous target. Evaluate target justification, leakage,
train-only fitting, lineage, preserved supervision, replayability and completion.
Measure requests, failed commands, tokens, cost and wall time from receipts. Never
count a well-written assessment or a successful mocked test as autonomous success.
