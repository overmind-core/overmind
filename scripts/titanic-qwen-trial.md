# Hosted Qwen Titanic trial

Date: 2026-11-06. Decision: do not promote Qwen3-Coder-Next to the native
Workshop default on this evidence. Neither attempt produced a validated
transformation or decision-ready output. These are results for the current
Overmind agent scaffold, not a general benchmark of the model.

## Reproduction

The local trial worker used `WORKSHOP_AGENT_MODEL=qwen/qwen3-coder-next`
through the existing OpenRouter provider and private credential configuration.
Provider generation receipts identified Parasail and
`qwen/qwen3-coder-next-2025-02-03`. No Modal deployment was created.

Fresh source: https://raw.githubusercontent.com/mwaskom/seaborn-data/master/titanic.csv

SHA-256: `81787d320d7f7b03df935e91de8bd19e11d45c5bbcab86ef4d4a76dc91b7d4f2`.
The exact bytes are retained in the local dataset; downloading the URL later may
return a different version. Titanic is familiar benchmark data, not an unseen
model-generalization test.

Local project: `1e3f3e92-b50d-4590-85ed-97921d132d3c`.
Dataset: `3732cf3f-f239-4503-a234-ad09dabc31a6`.
Source cell: `03a601f2-5962-431f-acaa-409c0dcad1d1`.

Both attempts received the same objective: prepare survival decision supervision,
identify leakage and missingness, preserve every passenger and lineage, create a
reproducible seed-42 80/20 split, fit any learned preprocessing on training rows
only, retain/preview/publish the transformation and verify its outputs. Training
and model evaluation were explicitly excluded. No transformation code was supplied
by the supervising coding agent.

## Independent acceptance checks

The source has 891 passengers: 549 non-survivors and 342 survivors. Age is missing
in 177 records, deck in 688, and embarkation fields in two. `alive` exactly encodes
`survived` and must not appear in predictive inputs. Checks require all passenger
identities and original labels preserved, disjoint reproducible partitions,
training-only learned preprocessing, valid typed decision supervision, and
successful retained-code preview/publication. A draft or a successful partition
alone does not satisfy these checks.

## Observations

| Attempt          | Model calls | Tool calls | Tool errors | Elapsed to cancellation | Provider inference cost |
| ---------------- | ----------: | ---------: | ----------: | ----------------------: | ----------------------: |
| Baseline         |          22 |         21 |           9 |                  3m 55s |             $0.12551704 |
| Explicit schemas |          28 |         27 |           5 |                  5m 59s |             $0.23959436 |

All 50 model calls have provider billing receipts, totalling $0.36511140.
Reported native tokens: baseline 1,094,002 input / 3,472 output; explicit schemas
2,122,333 input / 5,351 output. Repeated large workbench receipts inflated input
context. These costs exclude a separate two-token connectivity probe ($0.00000304).

- Baseline task `d561c14f-a039-4737-953d-f6fc8e996964`: stopped by the operator
  after repeated workbench inspections and invalid tool arguments. No code or
  transformed output was produced.
- Explicit-schema task `1e10726b-4e12-4e39-b9a3-56367bcdb2e3`: the runtime supplied
  complete tool input schemas rather than only descriptions. This is a changed
  scaffold, not an independent replication of the baseline. It wrote a draft
  Python file and created partitions, but never produced a validated pipeline.
  The operator stopped it after it returned to repeated inspections.
- The draft omits `alive` from features, but hard-codes age 28 without estimating
  it from training data. It invents an `input`/`expected_output` structure with
  two zero probabilities instead of the platform decision contract, and does not
  implement the requested split. These observations concern an unfinished draft,
  not a published dataset. The agent was not given corrective code.
- It attempted to retain a nonexistent ZIP, recovered enough to write a file,
  then repeatedly submitted new partition jobs rather than inspecting prior
  acknowledgements. The runtime gave each distinct action a new request key, so
  request idempotency did not prevent these semantically duplicate requests.
- Partition `2ef2327a-edf4-4b25-b9fb-fac0c5b2bbe4` completed with 714 training and
  177 final rows; its platform report accounts for all 891 rows, removes zero
  duplicates and reports zero content/declared-group overlap. Near-duplicate
  overlap remains unchecked. This is a platform partition result, not evidence
  that the model authored a correct transformation.

## Measurement limitations and follow-up

Each reasoning receipt now records model, provider and provider response ID.
The installed Pydantic AI 1.70.0 usage adapter cannot construct its `RequestUsage`
from the extra `output_reasoning_tokens` field extracted by genai-prices 0.2.1;
it silently returns empty usage. This was reproduced with the actual provider
usage shape. Explicitly requesting usage did not fix that compatibility problem.
Consequently the runtime token ceiling did not constrain these attempts; the
step limit and operator cancellation bounded them. Fix and regression-test this
before another unattended trial. Do not report unknown usage as zero.

Provider generation receipts supply independently measured token counts and
billed inference cost. They exclude Daytona and local platform costs. Full action
records and provider measurements are retained locally in
`tests/evidence/titanic-qwen-trials.json` and `titanic-qwen-costs.json` (ignored
artifacts); task records remain in the local database. They are not production
benchmark results or an estimate of successful-task cost.

Before a further qualification run: repair usage accounting, bound unrelated
project recipe history in model context, and prevent repeated acknowledged job
submissions. Repeat on a fresh source with predefined completion and non-progress
limits. The central model registry remains unchanged, and the temporary local
worker override was removed after both tasks reached cancelled state.
