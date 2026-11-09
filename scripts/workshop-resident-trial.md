# Resident Data Workshop qualification

This trial replaces the external per-turn agent dispatcher with `workshop_runtime/agent.py` in Daytona. The original objective is unchanged: **I want to train a decision model on this data**.

## Runtime evidence

The initial resident release is `sha256:a3d718e7d6d16bfbb54e610c943e53b5dc1234be2cecd8e466921420cb10065e`, workspace `5d79958f-41c3-48f2-80c3-33d351009dbe`.

`output/native-workshop/resident-qualification.json` records the LLM-free live run: shared Python state, an exception and repair, killing the resident runner during an active kernel execution, restoring the runner without repeating its file mutation, clean replay rejecting an undeclared scratch-file read, local partition assignment and runner exit. It used a scripted model transport and made no model API calls. The first qualification fixture used NumPy division by zero, which warns and returns infinity; it was corrected to Python integer division to test a real exception.

Local tests additionally verify task request-key conflicts and tenancy, actual kernel execution and recovery, and native publication through shared lineage/partition services. The publication fixture's retained script deliberately raises if executed: publication must consume the trusted replay output instead. A changed retained output is rejected.

## Systematic findings

- Workspace restoration previously discarded source ownership and then compared against it. Restore now preserves validated workspace UIDs and modes, rejecting unsafe members and setuid/setgid entries. The previous workspace remains stopped and retained.
- The coordinator accidentally imported a runtime module that loads kernel libraries. The external coordinator no longer requires the Daytona-only kernel dependencies.
- The initial context named a missing task.json. Startup now stages that file, and the native manifest consistently pins the workspace runtime.
- Package extensions and partition grouping semantics were not explicit enough in the local contract. Native context now describes accepted files and the difference between independent grouping keys and a compound tuple key.
- Local partitioning must carry inherited source contamination identities, including across transformations. Replay and publication now share provenance inheritance; the server independently verifies assignments.
- Publication must validate against the run's immutable release, not whatever source is currently checked out. Generation checks prevent stale workspace publication.
- Coordinator restart exposed a five-minute heartbeat wait. The replacement activity maintains an independent heartbeat during staging and model I/O, with a 30-second timeout for subsequent runs.
- Structured kernel output was unnecessarily double-encoded in model observations. The updated runtime renders stream/display/traceback text directly and retains full receipts as local files.

## Initial Titanic trial

The original retained CSV was exported and hash-verified, then uploaded into a new local dataset. No target, feature list, split recipe or transformation code was supplied to the agent.

- Dataset: `0c1eb5e8-769d-4f85-8ad6-bc6a3af9c36d`
- Source cell: `17f6bd11-873b-4d1d-b616-13388c9fee5f`
- Source fingerprint: `ba1536581313d6a1eb629b4dad871676004c60b7dfad89daf3597d5311629a48`
- Original CSV SHA-256: `81787d320d7f7b03df935e91de8bd19e11d45c5bbcab86ef4d4a76dc91b7d4f2`
- Initial task: `40caef2f-2a85-4e3f-8762-8835e7a3c65c`

The agent explored all 891 rows, identified alive as an exact target alias, selected seven boarding-time inputs, retained missingness and all observed labels, and measured duplicate/conflicting states. It repaired a rejected README.md package extension, an over-connected grouping recipe and an incorrect validator invocation itself. It produced a clean replay and checked all targets, source identities, state values and split overlaps. These are agent-authored checks until the independent published-output audit is complete.

The coordinator was paused and restarted to apply release pinning. No transformation code or data recipe was edited by the operator. Publication and independent audit results are recorded below.

## First published result

The resident runner completed 19 turns with 361,186 reported aggregate tokens. Four local errors were corrected by the agent: the missing task alias, the package extension, grouping semantics and the evaluation-reader invocation. Operator interventions repaired release pinning and terminal-state database locking, with coordinator restarts; no data recipe or transformation was edited. The kernel survived, and publication was not repeated.

Published primary cell: `45e3060f-ef85-4bfb-852a-e48b525cc841`; pipeline revision: `514d95aa-cf3d-417d-8b88-305c3e5aae8b`; partition: `56e07a7e-357d-4610-9bed-54198b68d285`.

`output/native-workshop/resident-first-audit.json` records an independent audit of the exported platform rows and retained package. All 891 source identities, labels and selected input values match. Age missingness (177) and embarkation missingness (2) remain explicit nulls. Both typed decision readers accept every row. A separate fresh Python process reproduces all published data values from the retained package. Train/development/final counts are 623/135/133, with no exact-input, declared-proxy-group or inherited-content overlap. Label counts are 403/220, 78/57 and 68/65 respectively; this class-balance drift limits comparison precision. Sixteen identical-input groups have conflicting observed labels; these remain observed outcomes rather than being fabricated or removed.

## Corrected-runtime trial

Release `sha256:0655620e5bdd876c88b3fae50224c2e55d1849d88cc45cbfac11f9826f2c0bd9` passed the original LLM-free qualification in workspace `d0cc1349-e65f-4447-bd2d-cb4335a46b88`. The preceding workspace was checkpointed, stopped and retained. Its checkpoint SHA-256 is `00a1237ea50b0218934b25c57a30f2fb62f172d8c17456cca0411f3a550a5741`.

The fresh dataset is `f4abd6eb-32d5-48f8-b768-bc8a2935288f`; task `46dc3c43-f06d-4835-b8d8-4a8cd9494402` receives the identical original CSV and minimal objective. Startup references were also corrected to describe local files and resident tools, removing obsolete planning gates and unavailable remote read tools. This trial has no operator edits to its data or recipe.

An expanded LLM-free regression exposed a further generality defect: partition assignment used raw transformed rows instead of rows with inherited provenance. Two transformation stages could therefore separate source groups that were no longer explicit output columns. The old runtime failed the independent group-membership assertion; the shared publisher would reject such assignments. The runtime now assigns from provenance-enriched rows. A first version of this regression fixture used the wrong group-key wire shape; that fixture failure is retained separately and is not evidence for the product defect.

## Fresh result and independent audit

The fresh task completed **13 resident turns, zero tool errors and 212,104 reported aggregate tokens**, with no operator intervention after task start. It independently chose a seed-42 stratified grouped split. Publication completed at 03:28:13 UTC and the task completed at 03:28:52 UTC on 7 November 2026; admission was at 03:25:14 UTC.

- Primary cell: `79c2dbb1-b030-4797-b8ab-53267dc26740`
- Output fingerprint: `281bc58c6de63d57bb54bcc225343330a595c4d680c1a8bc2c44abc6202ef26d`
- Retained pipeline: `9b6e3cb0-19e2-49b7-8ff7-92c50dd0a48c`
- Package: `77f1d1b0-73e9-4223-932b-92f1dfd790b2`, SHA-256 `bed3a99bdaee1caca808665a1fc88b9a50be2a5938e9f1e375dfa891b43281a4`
- Partition: `1ede43fe-97e8-4620-96a8-707a225db586`

| Role        | Rows | Non-survivors | Survivors |
| ----------- | ---: | ------------: | --------: |
| Train       |  624 |           384 |       240 |
| Development |  135 |            83 |        52 |
| Final       |  132 |            82 |        50 |

The independent `output/native-workshop/resident-fresh-audit.json` passes the same complete source/value/target/lineage, typed reader, fresh-process replay and split-isolation checks as the first audit. No records were dropped or labels invented. The retained question has real line breaks and stable option descriptions. Audit columns contain original outcomes, but the actual training consumer reads only the explicitly whitelisted `decision.state`, fixed question/options and target distribution.

The seven model inputs are pclass, sex, age, sibsp, parch, fare and embarked. `alive` and `survived` never enter state. Missing age/port values remain null. No imputation, scaling or other learned preprocessing is fitted. The target is an observed historical survival outcome represented as categorical supervision, not a calibrated probability or a recommendation about resource allocation.

This is a legitimate input for a controlled historical-survival decision-training experiment. It does not establish deployment suitability, causal validity or model performance. Family identities and collection timestamps are absent; the booking proxy is conservative but cannot certify household isolation. Source licence, near-duplicate entity linkage and pretrained-model exposure remain unverified. The 16 conflicting identical-input groups are retained and documented.

Compared with the earlier split-loop trial (162 steps and 1,596,569 aggregate tokens), this run used 13 turns and 212,104 aggregate tokens. These are single-run observations, not a controlled benchmark; the runtime, interfaces and observations changed. No training or evaluation job was launched.

## Final runtime regression

Final release: `sha256:cceee1595c7f5ab48fb4b3821c008984a302309864358ab8a86d1293c7fe18f5`; persistent workspace: `748bb730-a75b-417a-9b9e-19a91bca6c79`. The previous completed-trial workspace is stopped and retained. The verified checkpoint SHA-256 is `9a5e83cef2527ad57220ed4af93a55abb75e08e010fbe954d484a9dd441b391d`.

`output/native-workshop/resident-final-qualification.json` passes the expanded LLM-free regression: two transformations preserve inherited source groups in the final assignment, active-runner death does not duplicate the file mutation, the persistent kernel survives, and clean replay denies undeclared scratch access. The same inherited-group assertion failed on the prior release. The final fixture produces 10/2 rows in two independent clusters; all 12 transformed values are checked.

`output/native-workshop/resident-final-titanic-replay.json` verifies the final release against the fresh agent's exact transformation. Only the manifest runtime pin changed; the code, source, parameters and partition recipe stayed unchanged. All 891 output hashes and the full partition-assignment hash match the published artifact. This check used no LLM and did not publish another dataset version.

Repository verification: 33 focused resident/shared-data tests passed; the real Data Workshop journey passed (two upstream deprecation warnings); required pre-commit hooks passed. No commit or push was made. Full production recovery, arbitrary descendant cleanup, large-data memory bounds and coverage across other intents/modalities remain unqualified, as recorded in the architecture proposal.
