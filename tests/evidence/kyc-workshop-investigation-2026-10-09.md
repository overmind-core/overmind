# KYC Workshop user-journey investigation

Read-only post-run audit on 2026-10-09. This is an investigation of an actual native-agent session and its retained MCP receipts, not a newly executed preparation or training test. No application code, dataset, recipe, model selection or provider job was changed by this audit.

## Scope and environment

- Chat: `codex://threads/01a1230f-c09f-76b1-af17-c118ec31a2d3`, **Prepare KYC screener training data**.
- Actual request: “Use overmind to transform and prepat this data to train the KYC screener agent”.
- Local MCP: `http://localhost:8000/api/mcp/`, contract 5.2.0, 67 tools.
- Catalogue SHA-256: `f9a5923b7fa4164250eaee0180e6c6752c4770a986977886771c419baef7de83`.
- Project: `financial-services`, `e18b29b5-915d-45a7-80cd-77ffe6559205`.
- Dataset: `KYC screener entity matching`, `dece0a89-ab26-4293-aa3a-b292d3a278c9`.
- Source file: `/Users/tyleredwards/Downloads/sample_10000.json`, 42,174,160 bytes; SHA-256 `fe2f701f6034d3adaa586ef747d3c3855ca10fcbac1de97fa3dd7e487058c521`; explicit `pairs` array selected.
- Source cell: `c13a1cd1-b095-4c7d-b1fd-c4d298a748ac`; fingerprint `46fdca74f75a2eb35c1f1b49cafe84e250249d265e2a91b502b5b6c510e266ab`.
- Published cell: `576914a0-1c6b-437b-9057-e2cff33a49f0`; fingerprint `5d2551d24895bb991477fd3bb83bc6bfa58173512f647c88dd92d34a4f95c7c8`.

## Why the UI contains one transformation

MCP dataset inspection returns exactly two cells: Source 1.0 and Build KYC screening conversations 1.1. The retained manifest contains exactly one step, `build_kyc_conversations`, with `input: source`. The returned flow has one transformation node and one edge. There are no saved condition branches or intermediate transformation outputs to display.

The three recipe revisions are repairs to that same single step, not three successive transformations. Only revision 3 published a cell. Preview runs do not publish cells. The script serializes the full left/right records into a user message, adds an authored system instruction, and copies `judgement` to the assistant answer. It does not perform a separate leakage treatment, review selection, deduplication analysis, or partition construction.

Frontend source confirms that the notebook renders returned dataset cells, with an optional ancestor filter for a selected historical iteration. This audit found no evidence that published transformation cells were hidden. Direct visual verification was not completed: automatic approval review denied the browser request on the basis of the user's MCP-only instruction. No alternate browser access was attempted.

## Recorded friction and failures

| Observation                                              | Evidence and consequence                                                                                                                                                                                                                                                                        | Recommended correction                                                                                                                                                                                                   |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Missing skill path, then stale installed guidance        | Agent first read a nonexistent `~/.agents/skills/overmind-datasets/SKILL.md`, then the repository `.agents` copy. That installed copy describes only four declarative operations and the old external-script path. The SDK source skill already documents retained packages and explicit flows. | Synchronize installed guidance, verify version/digest against the source, and keep connected workflow contracts complete enough to use without repository source searches.                                               |
| Local byte transfer required a separate network approval | CLI preflight returned `network_permission_denied`; the same check succeeded with scoped host permission. MCP itself was connected.                                                                                                                                                             | Keep the cause explicit and support the host's scoped permission flow. MCP connectivity must not be presented as proof that the local transfer process can connect. Do not disable isolation.                            |
| First upload omitted project ID                          | `overmind dataset upload ... --dataset ... --json-rows-field pairs --json` failed with “Missing project-id.” The retry with `--project-id` worked.                                                                                                                                              | Include the resolved project in every copyable CLI handoff. The current upload resource's primary command and existing-dataset examples omit it.                                                                         |
| Package ID was copied incorrectly                        | The first `save_dataset_pipeline` used `ac554b74-cf4a-4c9d-bdcb-77ffe6559205`; the actual package was `ac554b74-cf4a-4c9d-bdcb-77f6d2b41905`. Correcting the ID recovered.                                                                                                                      | Native agent must forward returned structured identifiers. The upload already returned a structured next action; guessing or retyping identifiers is unnecessary.                                                        |
| First preview had an incompatible row-count assertion    | Revision 1 required `min_rows: 10000` while the agent requested a three-row preview. Container exited successfully; platform row checks rejected the result.                                                                                                                                    | Make preview/check scope explicit and detect this conflict before dispatch. Report the exact failed assertion and measured/required values. Do not silently weaken full-run checks.                                      |
| Second preview dropped lineage                           | Revision 2 emitted three rows but omitted `source_row`; platform rejected publication with the specific lineage error.                                                                                                                                                                          | Provide a minimal working retained-script example that preserves lineage. Static validation should explain its coverage and any missing declared lineage contract without pretending to prove arbitrary script behavior. |
| Queue wait prompted infrastructure inspection            | The agent inspected Docker after two status reads. The second preview was claimed about 4.1 seconds after creation; no stuck runner was established.                                                                                                                                            | Return queue age, runner freshness and a suggested polling interval so normal scheduling delay does not look like a failure.                                                                                             |
| Recipe is tied to this file size                         | Final manifest sets `max_rows: 10000`, despite otherwise performing a generic row-preserving mapping.                                                                                                                                                                                           | Separate dataset-specific acceptance checks from reusable transformation constraints. A future larger compatible source would currently fail this recipe.                                                                |

## Run receipts

| Revision / purpose | Run ID                                 | Outcome                             | Time from creation to last update     |
| ------------------ | -------------------------------------- | ----------------------------------- | ------------------------------------- |
| 1 / preview        | `853dcb8d-b0e1-4b1a-aacf-342e0ff857c4` | Failed: declared row checks         | 3.032 s                               |
| 2 / preview        | `2ded4ae9-5105-4d14-a6e8-ccede4e2a37c` | Failed: missing lineage             | 6.687 s                               |
| 3 / preview        | `6f94a740-9b67-489d-bcd9-1972c2db92a7` | Completed, no publication           | Not independently timed in this audit |
| 3 / publish        | `1e4779f0-09fb-4061-a52a-89603ddff842` | Completed; 10,000 input/output rows | 11.466 s                              |

Exact published revision: `6e29c6bc-dfcf-48fe-b264-995297c0fdb2`; family `ece8f610-dff8-4c46-9f5f-a359a9d5b23a`; retained package `0245f163-9dd4-4ddc-932d-1adc5ffafac8`. The successful step itself took approximately 6.4 seconds.

## Task suitability and reporting gaps

1. **Readiness wording overclaims the measured result.** Repeating the same read-only readiness check returns `ready: true` and “Fine-tuning is ready.” It also warns that the dataset has no current quality review and has not been reviewed for KYC Screener. Its `ready` flag is computed from technical blockers, not task suitability. The agent elevated this to “training-ready for the KYC Screener.” Expose technical readiness, task evidence and split coverage distinctly, without adding a universal approval gate.

1. **The task mismatch is concrete.** The capability description covers extracting KYC entity files, applying firm rules, screening parties and packaging compliance gaps. The selected existing evaluation set contains three tool-assisted identity-verification cases, expecting `approved` or `needs_review` and evidence. The new training outputs are only `positive`/`negative` entity-match labels. This data can support an entity-matching subtask; the session did not establish that it trains the complete capability or matches the existing evaluation task. The readiness response itself says `task_type: tool_calling` while `has_tool_calling: false`. No evaluation or training was launched by this audit.

1. **Identity shortcuts remain in every model input.** All 10,000 published user messages contain `id` and `referents`. Whole-source SQL found 515 positive pairs with identical IDs and zero such negatives. That is a measurable shortcut risk; whether these fields belong in inference inputs requires an explicit task decision. The script specifically instructs the model to use identifiers and referents. No claim of leakage removal is supported.

1. **No development split or entity grouping was constructed.** The readiness call explicitly set `validation_enabled: false`. Zero overlap with a three-row, different-task eval set does not establish a contamination-safe evaluation design. Entity/pair grouping and an appropriate held-out task remain unresolved.

1. **The preview had narrow coverage.** The three preview rows were all positive Occupancy pairs. Whole-source profiling includes Person/Person (3,750), Occupancy/Occupancy (2,839), Company/Company (962), Succession/Succession (473), and other same-/cross-schema families. The schema distribution query was limited to 25 groups and returned `truncated: true`; no claim of complete family enumeration is made. The final run did validate all 10,000 rows technically.

1. **The intended capability was never linked to the dataset.** Dataset inspection still returns `capability: null`, although readiness was called with the KYC Screener ID. Supplying a capability to a read-only check does not persist that association. The Console's training handoff reads the dataset capability, so this omission affects the next user action.

1. **Preparation finished after the chat stopped watching.** Existing preparation `030bac24-6c75-421e-9fbe-a95f660393a5` is now `ready`, taking 143.772 seconds from creation to last update. It prepared Qwen/Qwen3.5-27B LoRA with context 32,768: 10,000 rows, 12,229,790 total tokens, maximum 22,578 tokens, 70,000 supervised tokens, zero incompatible rows. This is successful tokenization, not training or measured model quality. The chat ended while it was still running and did not arrange a completion follow-up.

1. **Progress facts still lose useful detail.** Operational events show tokenization at 0/10,000, then ready about 104 seconds later, with no intermediate row progress in the retained timeline. The ready event loses total/completed counts, heartbeat is null, and both terminal pipeline and preparation job envelopes return `completed_at: null` even though terminal timestamps exist elsewhere. Those are observable reporting gaps; this audit did not determine the provider-side cause of missing intermediate tokenization events.

## Whole-output verification

Read-only MCP SQL over published cell 1.1 returned:

- 10,000 rows and 10,000 distinct `source_row` values, spanning 0–9,999.
- All 10,000 rows have three messages.
- All 10,000 assistant message values equal their retained `judgement`.
- All 10,000 user messages contain serialized `id` and `referents` fields.

Source labels: 7,690 positive, 2,310 negative. Same-caption negatives: 68. These can be valid hard examples; a shared caption alone does not justify dropping or relabeling them.

## Repeating this audit

Requires an authorized local Overmind MCP account with access to the project. Pass `project_id` on every call; no credentials belong in arguments or output.

1. `list_projects` and select the project above.
1. `inspect_dataset(dataset="dece0a89-ab26-4293-aa3a-b292d3a278c9", cell_limit=10)`; verify the two cells, active identity and `capability`.
1. `inspect_dataset_workbench(dataset="dece0a89-ab26-4293-aa3a-b292d3a278c9", pipeline="6e29c6bc-dfcf-48fe-b264-995297c0fdb2", limit=20)`; verify the single-step flow and three recipe revisions. Runs in this filtered response belong only to the selected exact revision.
1. `get_job(kind="dataset_pipeline", id=RUN_ID)` for each run above; inspect terminal error, steps, counts and timestamps.
1. `get_job(kind="training_preparation", id="030bac24-6c75-421e-9fbe-a95f660393a5")` and `inspect_operation(operation="7e2e2916-c465-497b-a7bb-3c071053b03e", limit=25)`.
1. `check_finetune_readiness(dataset="dece0a89-ab26-4293-aa3a-b292d3a278c9", cell="576914a0-1c6b-437b-9057-e2cff33a49f0", capability="43d35b0c-dd4b-4d45-b167-2ab657123702", validation_enabled=false, eval_incumbent_before=false, eval_incumbent_after=false)`. Readiness is a read-only check; do not call launch tools.
1. `query_dataset` over source cell 1.0:

```sql
SELECT judgement, COUNT(*) AS rows,
       COUNT(*) FILTER (WHERE json_extract_string("left", '$.id') =
                              json_extract_string("right", '$.id')) AS same_id,
       COUNT(*) FILTER (WHERE json_extract_string("left", '$.caption') =
                              json_extract_string("right", '$.caption')) AS same_caption
FROM t GROUP BY judgement ORDER BY judgement
```

8. `query_dataset` over published cell 1.1:

```sql
SELECT COUNT(*) AS total_rows,
       COUNT(DISTINCT source_row) AS distinct_source_rows,
       MIN(source_row) AS min_source_row, MAX(source_row) AS max_source_row,
       COUNT(*) FILTER (WHERE json_array_length(messages) = 3) AS three_message_rows,
       COUNT(*) FILTER (WHERE json_extract_string(messages, '$[2].content') = judgement) AS label_matches,
       COUNT(*) FILTER (WHERE contains(json_extract_string(messages, '$[1].content'), '"referents"')) AS inputs_with_referents,
       COUNT(*) FILTER (WHERE contains(json_extract_string(messages, '$[1].content'), '"id"')) AS inputs_with_ids
FROM t
```

9. Inspect all three existing eval rows with `query_dataset(dataset="af7dc707-165a-41bb-ac18-f13d86007740", cell="683f40ee-71ab-4176-b22f-3da36f1d255e", sql="SELECT source_row, input, expected_output FROM t", limit=3)`.

## Recommended implementation order

First repair the installed/native workflow guidance and CLI handoffs, then improve preview assertion diagnostics and reusable package examples. Expose task-fit evidence, unresolved split design and exact capability association alongside technical readiness. Retain meaningful authored intermediate steps and branches when the task needs them; do not add cells merely to make the graph look complex. Complete the operational timeline and arrange explicit follow-up for ongoing jobs. Validate the result with the same plain-language user request in a fresh native-agent session, measuring first-attempt success, avoidable retries, task suitability, published outputs and completion reporting.
