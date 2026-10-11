# KYC retained transformation verification

## Scope and diagnosis

Local API: `http://localhost:8000`; project: `e18b29b5-915d-45a7-80cd-77ffe6559205`.
Dataset: `119c8558-3bf7-5bb7-80de-a08393543f4c` (KYC training · thread-preserving).

The existing published transformation was **executed and reproducible**, but it was
a declarative recipe, not an uploaded Python package. Revision
`033236f1-4301-4772-8397-ef756144cac5` selected `tokens` and `kyc_risk_bucket`,
renamed them, and constructed conversations. Its package was null. This is not a
failed upload or an externally imported output falsely presented as execution.
It did not satisfy the user's requested retained, staged script entity.

The native coding agent authored the replacement. No platform authoring agent,
UI change, semantic relabelling, resplit, training or paid evaluation was involved.
The previous run and cells remain historical; their execution attribution is unchanged.

## Pinned inputs and retained entity

- Source cell: `138c21b7-8ad6-431b-b859-dae9bd169e61`.
- Source fingerprint: `bbcfc9af3f5f6a8b5aaf83080fb7aed0f2f770fe369342c4a8d1d13125b9bf98`.
- Rows: 224,003; original source export: 489,549,526 bytes.
- Previous final cell: `a2a88e32-88a3-477c-9795-e1bfa712c0f9`.
- Family: `249a9204-fc5e-4042-90b6-2525a0006a0f`.
- New revision 3: `7390916b-2b58-4ec6-9b0d-4fa5848683cc`.
- Package: `328fa690-6c54-4b40-811f-9584050e06d5`.
- ZIP SHA-256: `4851185695fefd7ac1862e65c9c439f26a15fd82b6c0b80d70aa2d2ee890f2c2`.
- Package size: 3,006 bytes, five files (manifest, README, three entrypoints).

Stages: validate supplied evidence/labels/identity → project existing input and
target fields → construct supervised user/assistant messages. Each stage retains
row identities, has `preserve_rows` checks, and exposes its substantive Python code.
The saved flow has no unconsumed steps. Runtime is pinned to the approved image
`sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea`.

## Checks and observed results

Failure cases considered before authoring: missing/null/blank/non-string evidence
or labels; invalid identities; changed Unicode/whitespace; swallowed duplicates;
large-integer identity loss; accidental interpretation of source instructions;
invented unknown-label mappings; nondeterministic replay; changed row order/count;
package-byte mismatch; platform execution differing from local execution.

- Local edge scenarios: **15 passed**, including expected rejection cases.
- Repeat execution: byte-identical.
- Full local replay: all **224,003** rows verified against source and the previous
  final output. `question`, `answer`, and `messages` are unchanged row for row.
- Canonical model-field digest:
  `5d7b313bccc1be55c7a52623f6b4e263c678e5861d4767e4ae30f31a2bb86a01`.
- Package upload, save, validation and exact download: passed; five retained files
  byte-match the authored files, and the archive checksum matches its receipt.
- MCP preview: `c445167c-c24d-48c4-b6f9-0e7edf9b6ee5`, completed in 2.675 seconds;
  100 rows at every stage, all preservation checks passed, 15 returned samples
  independently matched against source rows. Execution: isolated container.
- Initial full publish: `e7eeb393-c0eb-48cd-8f04-8a3b8875f237` failed in 10.685
  seconds with artifact-transfer exit code 137. The initial package declared only
  512 MiB memory despite the 489 MB source and a validation stage retaining every
  field. `/work` is tmpfs and counts against the memory limit: scratch size alone
  does not allocate additional memory. This is consistent with memory exhaustion;
  the receipt did not retain an explicit OOM flag. Inspection confirmed the previous
  active cell and all five existing cells were unchanged; nothing partially published.
- Revised package changes only the memory allowance to 2 GiB and documents it.
  Python entrypoint checksums are unchanged. New immutable revision and request
  keys retain the failed attempt without misrepresenting it as successful.
- Revised preview: `cde60d88-9b19-402e-9fd4-55fdd0abf91e`, passed in 2.471 seconds;
  100 rows per stage and 15 independently verified returned samples.
- Revised full publish: `162dee74-e75f-4392-930a-5846accd533a`; completed in **232.98
  seconds**, with 224,003 rows and passed preservation checks at every stage.
- New cells: validation `285c7666-4253-4c62-96cb-d10921b8bbd7` (1.5), projection
  `c0870d4f-0078-461a-a197-05484d944f02` (1.6), conversations
  `f7e2e943-5fc8-4a19-a14f-ee916dfd1cf6` (1.7, active).
- Inspected each published cell: actual Python script, correct entrypoint, package
  checksum, revision 3 and isolated-container run attribution are present.
- MCP aggregate checks: 224,003 distinct source identities; all four label counts
  unchanged; zero malformed conversations.
- Published export: 232,184,437 bytes. Full row-for-row verification against source
  and prior output passed with the same canonical model-field digest as local replay.
- Ruff, pre-commit and diff checks passed. No application behavior was changed;
  testing covered the authored scripts and their real local MCP lifecycle.

## Reproduction

Requirements: running local API and restricted Workshop runner with the pinned
image; authenticated local MCP/CLI access to the project; installed `overmind`;
repository Python environment (MCP SDK/httpx/anyio for the receipt check).
Local network permission must be granted by the host when sandboxed. Never use a
different endpoint or expose credentials to bypass that permission.

```sh
overmind connection check --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
overmind dataset export 119c8558-3bf7-5bb7-80de-a08393543f4c --cell 138c21b7-8ad6-431b-b859-dae9bd169e61 --output /ABS/NEW/source.jsonl --json
overmind dataset export 119c8558-3bf7-5bb7-80de-a08393543f4c --cell a2a88e32-88a3-477c-9795-e1bfa712c0f9 --output /ABS/NEW/baseline.jsonl --json
.venv/bin/python tests/evidence/kyc_retained_pipeline_check.py --source /ABS/NEW/source.jsonl --output /ABS/NEW/replay.jsonl --baseline /ABS/NEW/baseline.jsonl --replay
overmind dataset pipeline-download 328fa690-6c54-4b40-811f-9584050e06d5 --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --output /ABS/NEW/retained.zip --json
.venv/bin/python tests/evidence/kyc_retained_mcp_check.py cde60d88-9b19-402e-9fd4-55fdd0abf91e /ABS/NEW/source.jsonl
overmind dataset export 119c8558-3bf7-5bb7-80de-a08393543f4c --cell f7e2e943-5fc8-4a19-a14f-ee916dfd1cf6 --output /ABS/NEW/published.jsonl --json
.venv/bin/python tests/evidence/kyc_retained_pipeline_check.py --source /ABS/NEW/source.jsonl --output /ABS/NEW/published.jsonl --baseline /ABS/NEW/baseline.jsonl
.venv/bin/python tests/evidence/kyc_retained_mcp_check.py 162dee74-e75f-4392-930a-5846accd533a /ABS/NEW/source.jsonl
```

MCP registration used `save_dataset_pipeline` with the existing family, expected
revision 1, uploaded package, explicit dataset/project and request key
`kyc-119c8558-retained-python-revision-20261010`. `validate_dataset_pipeline` and
`run_dataset_pipeline` pinned the source cell/fingerprint above. Preview used 100
rows and request key `kyc-119c8558-retained-python-preview-20261010`; publish used
`kyc-119c8558-retained-python-publish-20261010`. Identical requests recover the
same receipts; do not change their arguments under those keys.
The revised registration used expected revision 2 and key
`kyc-119c8558-retained-python-2gib-revision-20261010`. Revised preview/publish keys
add `2gib-` before `preview`/`publish`; source, target and semantics stay pinned.

## Limitations and findings not changed

This preserves the existing **risk-bucket prediction** recipe. It does not establish
that text alone supports the labels, certify their correctness, or teach all of
the linked KYC Screener capability (entity extraction, rule evaluation, screening,
escalation). The existing capability-mismatch warning remains meaningful.

The native chat resource bridge truncated the large preview resource inside its
JSON string. Reading that same resource through an MCP SDK session returned valid
JSON and allowed verification; no REST/browser fallback was used. Large receipt
pagination remains a separate product-inspection concern, not a package failure.

Code and checks are in `tests/evidence/kyc_retained_pipeline/`,
`kyc_retained_pipeline_check.py` and `kyc_retained_mcp_check.py`. Local raw-data
exports remain in `/private/tmp/overmind-kyc-script.WwOfGW`, not in the repository.
Machine-readable receipts are in `kyc-retained-pipeline-receipts.json`.
