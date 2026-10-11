# Workshop user-journey acceptance

## Failure cases defined before implementation

- Upload commands omit the selected project, confuse dataset and package IDs, or stop at local files.
- Installed skill guidance disagrees with the connected workflow.
- Population row bounds fail a bounded preview, or relaxing previews silently removes publication checks.
- A script drops row lineage; static validation incorrectly claims runtime correctness.
- Routine queue waits produce retries or infrastructure investigation.
- Terminal jobs omit completion time/counts or continue suggesting polling.
- Technical format compatibility is reported as semantic task suitability.
- The requested capability is missing from the published dataset.
- Retained recipes contain only formatting while omitting necessary task interpretation, review and split design.
- Reuse or adaptation changes source data, loses parent identities, duplicates publication, or hardcodes the initial source size.

The narrow service regression exercises preview/publication checks through MCP registration and durable runs, with an identity executor to isolate population-check and publication behavior. Real container execution, CLI bytes and MCP transport are verified separately against stored local datasets.

## Planned live acceptance

Fan-in acceptance added before implementation: disjoint branches must converge with full row/label/group coverage and exact per-parent fingerprints; empty branches must remain valid; overlapping identities, unknown/duplicate parents and cyclic references must fail without publishing. The current view must show the selected output's ancestors, with condition chips immediately above the receiving cell. History remains accessible without mixing obsolete branches into the current result.

Use the saved account connection, the running local API and dedicated Workshop runner. Discover projects and real source identities through MCP. Author retained packages, upload local bytes using the installed CLI, preview and publish, then query whole outputs. Repeat each tested workflow three times with distinct deliberate run keys and retry identical keys to verify receipt recovery. Preserve original datasets; create clearly named acceptance datasets for repeat runs. Include the original entity-pair JSON, document evidence, structured/tabular data and existing capability examples. Label synthetic or derived fixtures explicitly. Do not launch training, evaluation or inference.

Record source identities, commands, recipes, row coverage, durations, unexpected errors, retries, output IDs and limitations. A clean replay demonstrates the tested paths; it does not prove that every future natural-language request is frictionless.

## Reproduction

Environment: local API at `http://localhost:8000`, saved account connection, installed editable CLI, approved isolated runtime and active Workshop controller. No injected credentials, direct database writes, browser automation or paid provider operations.

```sh
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_real_journeys.py --cli /Users/tyleredwards/.local/bin/overmind --run UNIQUE_RUN_NAME --repeats 3
```

The explicit CLI path avoids the older command in the repository's Python environment. A unique run name creates new, clearly labeled acceptance datasets; do not reuse it to request changed work. The supplied original `sample_10000.json` is used for the first pair upload when its verified bytes are available; subsequent pair uploads use the exported real source. Tabular uploads exercise CSV; document evidence and existing conversations exercise JSONL. The document scenario transforms retained extracted PDF evidence, not fresh PDF/OCR ingestion.

Supplemental cross-project/capability testing uses `--security-source` with the existing 900-row SOC dataset. Those records are explicitly synthetic and are not counted as real-data coverage.

## Fixes and observed friction

- Draft MCP results return exact project-bound upload argv; saved revisions and runs return scoped next actions.
- Dataset inspection and busy errors direct clients to the actual active transformation, not the completed landing job.
- Full-population row bounds are deferred in bounded previews, never silently removed from publication. Failure receipts retain expected and actual counts.
- Static validation warns about missing lineage declarations; the connected resource includes a lineage-preserving starter.
- Runs expose queue age, polling cadence and terminal time. Training readiness separates technical checks from unmeasured task suitability; terminal preparation jobs expose completion time.
- Source/project conflicts and changed request-key recipes have distinct error codes.
- Native prompts/server instructions require capability interpretation, meaningful stages and a verified platform publication; the repository's installed dataset skill was synchronized.
- The first isolated regression caught lost failed-check details; the fix passed retesting.
- Early harness attempts caught its own argument-name collision, CLI shadowing and an incorrect assumption that a busy controller was unavailable. These are recorded failures, not clean successes.
- The first live pilot used a stale long-running controller. The API had reloaded but the controller had not. The client was interrupted, its durable run completed, and the controller was restarted before verified repetitions. Live acceptance now requires the new check-result fields.

## Original dataset repair

Revision 4 below was an intermediate repair. It left review branches disconnected;
the user's review correctly identified that product failure. Revision 5 supersedes
it with a real converged recipe, not invented connector lines. See the measured
fan-in acceptance below. Historical versions remain intact.

Dataset `dece0a89-ab26-4293-aa3a-b292d3a278c9` is explicitly linked to KYC Screener. Recipe revision 4 is `52b1c425-bd89-45fb-9870-003770546391`; its earlier revisions remain intact. Preview `c2a3a5ad-7c1a-4699-8a13-2268e9e0bf9f` and publication `627f40c5-5807-436c-898a-ccdf26d6dc6b` completed.

Five new cells retain source inspection/group evidence, remove direct record-ID/referent shortcuts from model input, separate 119 review candidates from 9,881 unflagged observations, and publish all 10,000 observations as entity-matching messages. Active cell: `e53ff724-3134-4cfe-8864-52472c4578bc`, version `1.6`. Labels remain 7,690 positive and 2,310 negative. Neither source rows nor the previous output was removed. Review flags are advisory, not verified label errors. No claim is made that entity matching covers tool-assisted KYC approval decisions.

An interrupted client recovered completed run `62eac5a1-7553-4afe-a854-e22ad59d3187` with its identical key, without another publication. Changing its bound preview setting was rejected. A cross-project recipe lookup was also rejected without exposing the recipe.

## Capability gaps to address next

1. **Durable preparation context.** The dataset brief is retained, but an agent's source-task mapping, assumptions, intended checks and acceptance criteria are not a first-class editable MCP record. Store agent-authored claims with evidence and unknown/conflicting states, then link checks and output receipts to that record. Do not introduce a platform planning agent or treat writing a claim as proving it.
1. **Representative previews.** Preview selects a prefix. The first three pair rows had zero review flags; the full source had 119. Add explicit row selection or stratified preview inputs with recorded source identities. Never label prefix success as branch coverage.
1. **Branch contracts.** Disjoint fan-in now executes with per-parent receipts and overlap rejection. Whole-source coverage still requires native-agent verification or authored checks; the graph alone does not establish exhaustive conditions. Relational key joins and job-level conditional skipping remain unsupported.
1. **Runtime identity.** API catalogue identity does not prove a long-running controller loaded the same release. Expose controller release/build identity and compatibility independently of heartbeat/activity.
1. **Publication detail and capacity.** The current controller executes one script run at a time. Queue contention lengthens end-to-end time; publication performs full-frame measurements in its atomic transaction. Measure these separately before claiming a performance improvement or scaling guarantee.

The Docker detour in the original chat is plausibly influenced by repository development context; that causal explanation is not proven. Generic agent behavior must be tested in independent, minimal-context conversations before claiming zero natural-language friction. These replays validate actual MCP/CLI mechanics and authored recipes, not an independent-agent success rate.

## Final verification and converged training output

- Baseline local MCP/CLI: **12 successful real-source replays**, three each for 10,000 entity pairs, 320 retained PDF evidence rows, 891 Titanic rows and 1,000 aviation conversations. See `workshop-real-verified.json`.
- Separate cross-project coverage: **3 successful synthetic SOC replays**, 900 rows each, in `workshop-real-security-ready.json`.
- Fan-in: **1 bounded preview and 3 full 10,000-row publications passed**, including identical-key receipt recovery and whole-output comparisons. See `workshop-converged-results.json`.
- Final backend regression command below: **226 passed, 3 skipped**. Skips are opt-in standalone Docker tests, not passes. Actual live package runs exercised the isolated runtime.
- Selected frontend graph/motion tests: **9 passed**; typecheck, lint, design, controls and contrast checks passed. API client regenerated. Pre-commit checks passed before the final evidence update.

Saved revision: `adc61cd8-a909-46d9-87b3-ada1a58c1237` (family revision 5).
Final active cell after schema-guard verification: `36fb7d78-c61b-46cc-aa62-72e20167143f`, version `1.26`.
Flow: source → audit → model evidence → two training-example branches → final training data.
Branches contain 119 review-flagged and 9,881 unflagged observations. Every branch
feeds the final output; no labels, groups, duplicates or review flags were lost.
All 10,000 row contents match revision 4, excluding regenerated platform provenance.
Every final row's parent cell and fingerprint match its actual branch receipt.
Review flags remain unresolved; entity matching is not the full KYC approval task.

The first fan-in harness attempt failed before registering the recipe because its
helper argument collided with `name`; that harness bug was corrected before the
successful run. The service regression also caught a no-op fingerprint regression,
which was corrected and retested. Failed attempts are not counted as clean passes.

The UI now defaults to the active output's recorded ancestors (six cells here),
with history behind All iterations. Browser inspection confirmed sibling branches
on one horizontal layer, orthogonal connectors, and bordered condition chips
immediately before receiving cells. A first visual pass caught chips overlapping
the header region; they were moved above it. Existing cell markup is unchanged.

Full publications took **55.840, 60.799 and 50.525 seconds** from MCP submission to
observed completion. These include polling and local contention, not export verification.
Recorded publication alone took 10.657–11.244 seconds; impact measurement took
8.737–10.926 seconds. The workflow is correct on these inputs but not instant.
Stage timings now accumulate repeated stages/heartbeats instead of overwriting
them. These observations do not establish multi-user capacity or maximum-size throughput.

A final schema audit found that the row store's mixed-scalar inference could coerce
disjoint branch values. Fan-in now rejects incompatible nonempty data schemas
before concatenation. A regression with numeric/string branches verifies failure
without publishing; agents must normalize the branches explicitly.
After reloading the idle controller, another preview and full 10,000-row run passed
with the guard active. Publication `48b540e4-61fa-45be-86e2-f778f32880c5` took 55.718
seconds observed end-to-end. `workshop-converged-schema-results.json` records the
final run and MCP server version 5.4.0. There are four successful full fan-in runs
in total, including this post-fix replay. The same-key recovery command was also
executed successfully without new publication.

Dark and light connector treatments were inspected; the original dark theme was
restored. Following generated-client regeneration, temporary Vite hot-reload
errors cleared after a page reload; no new browser errors appeared in the check.

Repeat the exact receipts without publishing new cells:

```sh
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_converged_journey.py --pipeline adc61cd8-a909-46d9-87b3-ada1a58c1237 --report /private/tmp/workshop-converged-recovery.json
```

To deliberately execute again, supply a new `--run UNIQUE_PREFIX`; this appends
new immutable iterations to the same explicitly identified dataset.

```sh
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_reusable_workshop_journey.py tests/test_mcp_catalog.py tests/test_mcp_resources.py tests/test_mcp_prompts.py tests/test_mcp_manifest.py tests/test_workshop_redesign.py tests/test_mcp_datasets.py tests/test_workshop_package_transfer_journey.py tests/test_workshop_readiness.py tests/test_decision_workshop.py tests/test_mcp_finetuning.py tests/test_mcp_workflow_friction.py tests/test_mcp_account_access.py tests/test_mcp_finetuning_receipts.py tests/test_dataset_transfers.py -q
```
