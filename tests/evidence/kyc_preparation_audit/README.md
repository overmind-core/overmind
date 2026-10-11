# KYC preparation checks: live audit

Historical audit of the preparation-check feature, subsequently removed at the user's request. The findings replay drivers were retired with that feature. Retained receipts and packages document the observed behavior; `verify.py` still verifies the exported data. See `../workshop-checks-removal.md` for current behavior.

Executed locally on 2026-10-10 PDT (receipts use 2026-10-11 UTC). No training,
inference, evaluation, model activation, deployment, commit or push was performed.
Product code and UI were not changed by this audit.

## Finding

The checks are useful for technical safeguards and for retaining specific agent
findings. They do not automatically assess whether a transformation teaches the
right task. The original recipe earned five passes while carrying internal
identity metadata in every model input. A preview with reversed labels also
passed the declared decision-training format check.

| Test                                                 | Observed outcome                                                                          | Scope of evidence                                                                                          |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Original JSON upload with explicit `pairs` selection | 10,000 rows landed; every nested source record matched the original                       | Full-file comparison                                                                                       |
| Previous conversation recipe                         | Five passes; task suitability not assessed; all 10,000 inputs contained IDs and referents | Full publication and aggregate query                                                                       |
| Drop one preview row with `preserve_rows=true`       | Rejected: expected 100, actual 99                                                         | 100-row preview; no cell published                                                                         |
| Decision probabilities `[1, 1]`                      | Rejected: probabilities must be normalized and match options                              | Preview; no cell published                                                                                 |
| Reverse valid positive/negative targets              | Accepted; consumer check passed for 100 rows                                              | All four returned preview examples independently confirmed incorrect; full preview output was not exported |
| Corrected three-step preparation                     | Completed in 35.649 seconds; 10,000 rows per step; decision consumer passed               | Platform isolated-container execution                                                                      |
| Independent corrected-output verification            | All records, labels, lineage, input policy and review flags verified                      | Full exported output against original source                                                               |
| Agent finding inheritance                            | All five concerns propagated from source into the final cell                              | MCP findings and exact REST parity                                                                         |
| Evidence-backed resolution                           | Only top-level identity treatment resolved; other concerns remain                         | Fingerprint-bound resolution and exact REST parity                                                         |

Preparation status at completion: **5 passed, 3 unresolved, 2 not assessed**.
The same number of passes as the original recipe represents different evidence;
these counts are not a data-quality score.

## Data and remaining concerns

Project: financial-services (`e18b29b5-915d-45a7-80cd-77ffe6559205`).
Capability: KYC Screener (`43d35b0c-dd4b-4d45-b167-2ab657123702`).
[Local dataset](http://localhost:5173/datasets/7b876858-e8a2-4d60-8361-ecee802e9b90/):
KYC matching · preparation check audit.

Original file: `/Users/tyleredwards/Downloads/sample_10000.json`, 42,174,160 bytes,
SHA256 `fe2f701f6034d3adaa586ef747d3c3855ca10fcbac1de97fa3dd7e487058c521`.
The wrapper's metadata identifies `pairs-20251209.json.gz`, seed 42 and the
7,690/2,310 class balance. These match the corpus and sampling description on the
[Hugging Face dataset card](https://huggingface.co/datasets/sanctions-er-anon/opensanctions_pairs/blob/main/README.md).
The current repository lists a 1,000-row sample, not this 10,000-row file. The
exact download/derivation of this local file was not independently authenticated.
[OpenSanctions' original-pairs documentation](https://www.opensanctions.org/docs/opensource/pairs/)
defines the labels as same/different entities. This supports their matching
interpretation, not independent verification of each supplied label.

- KYC Screener includes extraction, firm rules, screening and escalation. Matching
  judgements cover a subtask, not the whole capability or approval decisions.
- 68 negative pairs have identical captions. They remain labelled negative and
  marked for review; caption equality does not justify correcting them.
- 27 repeated unordered entity-ID pairs cover 55 observations. All are retained.
  This identity-based definition differs from full-record duplicate counts.
- Connected IDs and referents form 8,954 groups; 51,203 identifier assignments
  were checked for consistency. No held-out split was created or certified.
- All model inputs contain only caption, schema and properties per entity.
  Original complete records remain available outside the model input.
- 2,759 rows still contain property values equal to source IDs/referents,
  including Wikidata identifiers and relationship fields. These may be legitimate
  evidence; the effect on generalisation remains unmeasured.

## Consistency gap found, not changed

`check_finetune_readiness` reports technical readiness and unmeasured suitability,
with a warning that all rows do not match the capability. The cell preparation
checks do not automatically include that capability warning. The warning comes
from the existing transcript/system-prompt/tool contract: this decision output
has no messages column. It is not a semantic task-coverage assessment.

The separate “No current workshop quality review” warning also remains after
recording new preparation findings because the old quality-review contract and
new preparation findings are separate mechanisms. The test verifies this
distinction rather than treating an agent note as automatic quality approval.

Recommendation: keep the current cell/source/status presentation. Populate it
with scoped, runnable source-to-output checks and explicit task findings; expose
existing applicable readiness warnings consistently. Preserve the distinction
between platform verification, agent assessment and unmeasured quality.

## Reproduction

Requires the existing local Docker deployment, available isolated Workshop
runner, address-bound account connection, installed Overmind CLI and repository
Python environment with the MCP SDK. Both repository and repository-free CLI
connection checks passed against `http://localhost:8000`; no credentials are in
these artifacts. Packages pin runtime
`sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea`.

Exact uploaded/retained packages are included as `controls.zip` and
`preparation.zip`; their hashes and complete execution receipts are in
`receipts.json`. The preparation package adapts the first three scripts of
retained revision `4b3ad974-0c1c-4868-a962-753811d4bc81`, omits its sampling step,
adds a decision consumer check and uses 2 GiB memory/scratch. Script bytes are
unchanged. The audit source, package IDs and request keys remain recorded in the receipts.

```bash
overmind connection check --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
overmind dataset upload /Users/tyleredwards/Downloads/sample_10000.json --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --dataset 7b876858-e8a2-4d60-8361-ecee802e9b90 --json-rows-field pairs --request-key kyc-check-audit-20261010-source --json --wait
overmind dataset export 7b876858-e8a2-4d60-8361-ecee802e9b90 --cell 36523ae9-6675-49aa-9ac8-7d497d47aa6a --output /private/tmp/kyc-audit-landed.jsonl --json
overmind dataset export 7b876858-e8a2-4d60-8361-ecee802e9b90 --cell 63806e31-001c-4272-91c5-014db4b7fefb --output /private/tmp/kyc-audit-prepared.jsonl --json
python3 tests/evidence/kyc_preparation_audit/verify.py --original /Users/tyleredwards/Downloads/sample_10000.json --landed /private/tmp/kyc-audit-landed.jsonl --prepared /private/tmp/kyc-audit-prepared.jsonl
```

Exports refuse overwrite: reuse verified files or choose new paths and update the
verification arguments. Retained package upload commands for a separate replay:

```bash
overmind dataset pipeline-upload tests/evidence/kyc_preparation_audit/controls.zip --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
overmind dataset pipeline-upload tests/evidence/kyc_preparation_audit/preparation.zip --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
```

The final process graph contains the source and three successful preparation
cells. The baseline is excluded from that graph; failed/semantic-fault previews
never published cells. Final cell: `63806e31-001c-4272-91c5-014db4b7fefb`.

## Failures investigated during the audit

The sandbox initially denied local network access; the identical connection
check succeeded with scoped host permission. Test-client mistakes were repaired
and rerun: export does not take `--project-id`; `list_projects` is unscoped; a
Python helper parameter collided with the tool's `name`; and revision updates
cannot simultaneously specify `derived_from`. No failed mutation was silently
treated as success. Verification initially compared the whole provenance object;
inspection established that execution correctly appends parent/content facts.
The repaired check verifies retained file provenance, original content keys and
the exact new parent receipt on every row. All these repaired stages passed.
The two deliberately invalid previews remain failed as expected and publish no
data; their corrected full preparation passed.
