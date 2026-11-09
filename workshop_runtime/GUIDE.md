# Data Workshop runtime

This environment is built for preparing model-training data. Dependencies are
installed and locked during image construction. Network access is blocked during
work. Do not run pip, apt, model downloads or package-manager commands. If a
required capability is missing, report it for a new tested runtime release.

Use Python 3.13 with pandas, Polars, NumPy, Arrow, DuckDB, SciPy, scikit-learn,
Matplotlib (Agg backend), openpyxl, RapidFuzz, datasketch, ftfy, regex, jsonschema
and tiktoken. cl100k_base and o200k_base tokenizer caches are installed offline.
Read runtime.json for exact versions and readiness, and native.json for the
resident replay and publication contract. Files and the persistent Python kernel share the task root;
use relative paths without prepending work/.

## Explore with evidence

The platform has already staged data/input.parquet and retained originals. Read input.json for their identities; no initial export is needed. The Parquet contains source_row identities. Use python with Python code; variables survive between calls. Errors return tracebacks and do not remove your execution tools. After a kernel reset, reconstruct variables from pinned data and saved modules. Sources and platform context are read-only; put authored files in the working directory. In examples below, source.parquet means the staged input path.
Use `python -m workshop_runtime.data profile source.parquet --target TARGET` to
measure schema, missingness, cardinalities, duplicates and explicit target counts
in one pass. This full-frame helper is for data that fits memory; use Polars lazy
scans, Arrow batches or DuckDB queries for larger files. Never label a sample as a
full-source profile. Save scripts, measured results and the seed in the workspace.

Establish the prediction unit, target meaning and features available at prediction
time. Look for direct target copies, proxies, repeated entities, time leakage and
near-duplicates. The profile only identifies exact same-valued target columns; it
does not certify absence of leakage. Treat unknown target meaning as unknown.

Pass a partition recipe to replay for local contamination-aware source-row split assignments. Split
before fitting imputers, scalers, vocabularies or encoders; fit on training only,
then apply the frozen transformation to evaluation. Persist the fitted parameters
and seed as inspectable JSON. Test missing values, unseen categories, nulls,
duplicates and boundary values. Never invent supervision or silently remove rows.

## Execute the publication interface locally

`python -m workshop_runtime.data stage source.parquet input.jsonl` preserves
source_row and removes only platform-managed \_overmind_provenance. Never recreate
source identities from output positions. For joins/aggregations retain all parents
with \_overmind_parent_rows and use replay followed by publication to validate that lineage.

Every retained entrypoint receives three positional paths:
`python transform.py input.jsonl output.jsonl parameters.json`.
Read those arguments rather than hard-coding task paths. Write one finite JSON
object per line. The same locked data libraries are available in publication.

`python -m workshop_runtime.data run-step transform.py input.jsonl output.jsonl parameters.json`
executes that interface. `python -m workshop_runtime.data validate input.jsonl output.jsonl --decision`
checks row-preserving identities and the shared typed decision contract. Omit
--decision for intermediate data. This helper is for one-to-one transformations;
use replay followed by publication for fan-out, joins and aggregates. It does not prove semantics.

Decision supervision lives in a `decision` object with state, question, kind,
options and explicit supervision. Use `modal_shared.decisions.decision_line` to
validate the exact contract used by training. Preserve probability targets, option
order, weights and provenance. Do not substitute ad-hoc input/expected_output
objects or zeros for target probabilities. Raw numeric features are not a decision
training dataset. The platform's decision_train/decision_eval consumer check is
still required on the final pipeline step.

Retain scripts and manifest.json in a ZIP using Python zipfile. Pin the immutable
workspace runtime ID from native.json. Run replay before publication. Verify published counts, labels, identities, disjoint
splits and consumer compatibility. Formatting success is not semantic correctness.

Intermediate steps are materialized as tables: absent columns become null, and mixed integer/float columns use floats unless that would lose integer precision. `_overmind_parent_rows` is consumed when lineage is assigned. A successor receives the assigned `source_row`; preserve it or declare parents from that immediate input. Keep original ancestry in a separate audit field if needed.
