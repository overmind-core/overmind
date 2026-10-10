# Decision Workshop acceptance

## Scope and failure cases recorded before changes

Exercise the local MCP and retained, isolated Workshop runtime using existing local datasets. Keep parent datasets and active versions unchanged. Verify published bytes through the installed dataset export CLI.

- Mixed decisions: preserve all 15,000 original validation observations, full soft distributions, option ordering, empty states, family/source/license metadata and group identities. Investigate the historical chat output's 22 missing rows. Unknown target interpretation must remain unknown.
- Titanic: preserve all 891 observations; derive the observed Survived class without leaking Survived, Name, PassengerId or Ticket into model inputs. Missing values stay unknown and Ticket remains available for grouped partitions.
- Entity matching: reuse the retained audited recipe over all 10,000 pairs. Preserve supplied judgements and connected identity groups; matching labels do not establish KYC approval truth.
- Training handoff: technical readiness must identify typed decisions; calibration/final members must expose requests without targets and lossless references. Verify exact row coverage and disjoint declared groups.
- Negative controls: invalid distributions, ambiguous labels and unsupported semantic claims must never become fabricated labels or silently disappear. A failed script must leave source and active consumer versions readable.
- Shared interpretation: profiling, impact measurement, partition projection and training validation must agree on supervision, including explicit weights and mean-only targets. A transformation that changes supervision must be reported.

Environment: local API http://localhost:8000, MCP http://localhost:8000/api/mcp/, installed `overmind` CLI with its address-bound saved account connection, existing Docker Compose stack and approved Workshop runtime. No Console actions or model activation. This test does not require another GPU training run.

Inventory: 428 datasets across nine local projects. Primary projects: overmind `1e3f3e92-b50d-4590-85ed-97921d132d3c`, financial-services `e18b29b5-915d-45a7-80cd-77ffe6559205`.

## Findings and repairs

The supported path is native-agent-authored retained Python, executed by the isolated Workshop runtime. A written task does not trigger an autonomous backend planner. The agent chooses the target contract from source and task evidence; MCP then validates, previews, publishes and hands exact versions to Training.

The historical mixed-source chat output contains 14,978 rows. Its 22 missing observations all have tied maximum target probabilities; the complete original 15,000 rows are technically valid decision examples. New typed recipes preserve all rows, 1,414 soft targets, 33 tied maxima, 149 blank states and options up to 235 choices. Historical outputs remain unchanged. Target interpretation is explicitly unverified; numerical preservation does not establish votes, posteriors or semantic truth.

Two product failures were reproduced before their fixes:

1. Flat decision interpretation discarded weights and target semantic/provenance fields. This also hid altered supervision from impact measurement and let a soft target declared as categorical gold appear valid in profiles. Shared interpretation now retains these fields. Two focused failing checks passed after repair.
1. Bumping the profiler cache version broke recovery of an unchanged saved request key. MCP reproduced this for both profile and derivation operations. Recovery now compares the user-supplied configuration; fresh requests use the new cache version while existing receipts remain frozen. Both failing MCP checks passed after repair.

Connected authoring guidance now explicitly selects typed decisions for decision/Jev tasks, preserves ties and uses retained scripts/output metadata for semantic evidence. It no longer directs authors to write historical preparation plans. Shared service changes apply to REST and MCP; no API schema, generated client, queue topology, migration or demo-seed change was required.

The changed data-format fingerprint required a new worker deployment. The first preparation correctly failed because that pinned app was absent. After deploying training and decision-evaluation apps to `overmind-dev`, retrying the same preparation reached `ready`: 713 train/development examples, 130,530 tokens, maximum 198 tokens, zero incompatible rows and the Unsloth Clef renderer. The trainer and processor fingerprints are unchanged. The harness also corrected an unsupported 1,024-token request to the API's supported 4,096 setting, and recognizes preparation's terminal `ready` status.

## Reproduction

```bash
overmind connection check --project-id 1e3f3e92-b50d-4590-85ed-97921d132d3c --json
PYTHONPATH=. .venv/bin/python tests/evidence/decision_workshop_live.py --request-prefix decision-workshop-20261010-verified --output tests/evidence/decision-workshop-verified-results.json
.venv/bin/pytest tests/test_decision_workshop.py tests/test_data_first_workflow.py tests/test_mcp_research_journey.py tests/test_mcp_prompts.py tests/test_reusable_workshop_journey.py tests/test_workshop_mcp_acceptance.py tests/test_workshop_readiness.py -q --no-cov
```

The script reuses existing request keys and completed receipts; a fresh prefix creates independent derived chains. It exports exact source/output cells with the installed CLI, compares every row independently, verifies grouped partitions and evaluation references, checks typed training readiness, and performs actual model-specific token preparation. It reuses the existing matching recipe and records `derived_from` attribution for formatted variants of the two new recipes. Source UUIDs, package hashes, runtime identity, step receipts, exports and output IDs are retained in the result JSON.

Regression run: **119 passed, 3 skipped** in 19.89 seconds. The skipped tests require an opt-in Docker test image; real isolated execution was separately exercised through the local MCP. Repository pre-commit checks passed. No frontend behavior changed, so no browser or frontend suite was run.

## Verified outputs

The final live replay passed. Every published row was compared with its original source; all three outputs passed typed-decision readiness. Original active versions were unchanged.

| Source                | Rows verified | Retained result                                                                             |
| --------------------- | ------------: | ------------------------------------------------------------------------------------------- |
| Mixed decision corpus |        15,000 | Dataset `042818ab-9832-5ef8-a3c9-fe65bb661081`, cell `253cb09a-ee40-430c-878d-d5d06978288e` |
| Titanic               |           891 | Dataset `e4e723e5-8f57-543e-92f6-5e6dd52fd287`, cell `316f2d3a-c976-4f47-b195-23ab232af0cb` |
| Entity matching       |        10,000 | Dataset `ff8a6988-d45b-5442-9114-cf7076d62f11`, cell `17e2a57b-60fd-4db0-ad65-8cabccec9c80` |

Titanic retains 708 rows with missing features. Its train/development/calibration/final counts are 624/89/89/89. Entity matching retains 7,690 positive and 2,310 negative judgements, plus 123 flagged rows; role counts are 7,000/1,000/1,000/1,000. All observations survive partitioning, no declared group crosses roles, and every calibration/final request and reference was verified independently.

Final Unsloth preparation `4cc4e2cc-619d-4d15-a66c-d8015cb0771e` is ready. Operation `cc2650af-62a9-4a25-aef1-717f387f3e81` exposes 16 recorded events and terminal 713/713 row counters through MCP. No missing heartbeat measurement was fabricated. Training app `overmind-sft-07e8a773f7d959be0cef1d5d` and native-evaluation app `overmind-decision-eval-962d3fa005915fcedbe08961` were deployed in `overmind-dev`.

Artifacts: `decision-workshop-verified-results.json`, `decision-workshop-investigation.json`, `decision-workshop-preparation-operation.json` and the retained package directories beside this report. `decision-workshop-live-results.json` preserves the earlier successful replay before formatting the attributed recipe variants.

## Limits

This checks transformation and training handoff, not model quality or the truth of source labels. The mixed corpus's target interpretation remains flagged. Pair judgements teach the supplied entity-matching task, not KYC approval. Missing Titanic features remain missing. Exact content and declared groups are checked; near duplicates and foundation pretraining contamination remain unmeasured. No new GPU training run was needed for this Workshop investigation.
