# Local skills, compact observations and verified briefings

Historical benchmark. See the [implementation audit](workshop-speed-implementation.md) for the current deployed configuration and the status of follow-up work.

The native Daytona runtime now loads local Agent Skills alongside exploratory Python calls, retains complete receipts behind compact structured observations, and starts with measured facts bound to the exact source. OpenRouter and the model remain unchanged. No model-call or token budget was introduced.

## Implementation

- Decision and conversation skills use the Agent Skills `SKILL.md` format. Their consumer contracts come from the platform's canonical definitions. The initial prefix contains a catalog and replay contract; a selected skill returns intact with the first relevant exploration result, without a separate model turn. Full taxonomy remains available locally.
- Compact observations preserve valid JSON, explicit omissions, errors and a local full-receipt reference. Explicit paginated reads preserve the requested contents. The durable history stays append-only; no summarizer model or history rewrite is involved.
- Briefings measure source structure, nulls, blank strings and exact duplicates, with bounded examples. Cache identity includes project, dataset, cell, file hash, runtime and profiler version. Every reuse verifies the current source bytes; invalid caches are recomputed. These facts do not infer targets or certify semantic quality.
- Exploration preflight and clean replay now share the same input projection. Platform-managed metadata is excluded consistently, preventing code from accidentally depending on provenance that replay cannot read. Required audit mappings must be retained explicitly in the package.
- Initial context omits duplicate source profiles, taxonomy examples and consumer contracts. Full local files remain available, and skill contracts return intact alongside exploration. The final context names installed helpers and explicitly distinguishes local structural checks from full platform chat validation during publication.
- Publication compresses replay artifacts for transfer, verifies the archive checksum/member list, then performs the original package, canonical-output, assignment and lineage checks. Temporary archives are removed; retained artifacts are unchanged.

This uses the existing Pydantic AI durable loop. The newer Pydantic Harness package requires a major Pydantic AI upgrade and was not introduced into this performance comparison.

## Validation and measurement

The focused suite passed 30 tests and the existing work-once journeys passed both tests. Checks cover source changes, corrupted briefing caches, project isolation, bounded observations with recoverable full evidence, local skills, persistent execution and recovery, compressed transfer fidelity and corruption rejection. All 15 live no-model checks passed, including killed-runner recovery, retained kernel state, intact local skills, complete evidence behind compact observations, clean replay isolation and cancellation. The live qualification and trial records are retained under `output/workshop-skills/`. The full repository suite and frontend tests were not run.

Task time is creation through terminal completion, including preparation, model calls, tools, clean replay, independent verification and publication. Upload, image build and workspace upgrade are excluded. Costs are provider-reported model charges; persistent Daytona and local infrastructure are excluded. Every attempted model run is retained.

The comparison baseline is the previous final context revision: Titanic mean 97.873299 seconds / $0.1065136 across three passing runs; Penguins 106.513113 seconds / $0.1056947; Dolly 170.838420 seconds / $0.1776719. Source bytes and minimal intents are unchanged. The trials use fresh local dataset copies, not warmed briefing caches.

## Final comparison

The final unchanged revision passes all three independent output audits. Dolly improves both time and model cost; Titanic and Penguins remain slower than the previous historical baseline. This is not a demonstrated general latency improvement. The implementation is deployed, with the remaining timing regressions explicitly retained rather than described as a speed win.

| Dataset  | Previous completion | Final completion | Previous model cost | Final model cost | Calls | Full audit |
| -------- | ------------------: | ---------------: | ------------------: | ---------------: | ----: | ---------- |
| Titanic  |            97.873 s |        130.969 s |          $0.1065136 |       $0.0967781 |     8 | Pass       |
| Penguins |           106.513 s |        143.253 s |          $0.1056947 |       $0.1126783 |     9 | Pass       |
| Dolly    |           170.838 s |        131.051 s |          $0.1776719 |       $0.0898338 |     7 | Pass       |

Baseline Titanic is the mean of three passing runs; baseline Penguins and Dolly each have one run. Final results are one fresh run per dataset on the same unchanged revision, not best-of selection or a statistical latency guarantee. Model, route and standard/default serving remain `openai/gpt-6.1-sol` through OpenRouter. No additional summarizer/model service was added.

| Dataset  | Model time, previous → final | Final setup | Final publication action | Final first-request input tokens |
| -------- | ---------------------------: | ----------: | -----------------------: | -------------------------------: |
| Titanic  |            66.999 → 79.691 s |    16.734 s |                  7.624 s |                     10860 → 6150 |
| Penguins |            75.494 → 91.879 s |    16.708 s |                  5.582 s |                     10797 → 5741 |
| Dolly    |           118.467 → 68.901 s |    28.030 s |                  9.786 s |                     12700 → 5648 |

The smaller first request is not the entire context saving: selected skill contracts arrive with exploration, and append-only history still grows. Provider-reported complete-run costs above include every actual model call.

Titanic F used eight calls versus seven in the baseline; Penguins D used nine versus seven, including separate grouping and replay-inspection steps. Their costs and wall time are included even though the immediately preceding Titanic E/Penguins C runs were faster and cheaper. Dolly D used seven calls versus ten, with no validator-discovery detour or tool exception. Remaining time outside model calls includes source staging, fresh runner/kernel startup, local verification, replay, transport and publication; the available measurements do not justify assigning all of it to external network latency. Historical transport conditions also differ.

| Final output | Records | Train / development / final | Verification                                                                                                              |
| ------------ | ------: | --------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Titanic      |     891 | 624 / 133 / 134             | All selected features/observed targets preserved; consumer and lineage checks pass; 704 near pairs, zero cross-role pairs |
| Penguins     |     344 | 243 / 51 / 50               | All selected features/observed targets preserved; consumer and lineage checks pass                                        |
| Dolly        |    1000 | 699 / 151 / 150             | All source fields/messages preserved; consumer and lineage checks pass                                                    |

The report retains role-specific cells and caveats; reader compatibility does not establish predictive accuracy. No training or evaluation job was started.

## Regressions investigated

Initial Titanic passed but needed an audit-report repair because NumPy integers were not JSON serializable. The native contract now names the installed JSON-value converter. Initial Dolly passed but attempted unavailable ripgrep; the contract names installed Python file-search tools instead.

Initial Dolly spent 62.12 seconds in publication. After compressed artifact transfer was introduced, the next Dolly publication took 7.04 seconds with the same independent hash and lineage checks. These are different stochastic runs, not an isolated transport benchmark. A separate read of the first retained Dolly replay verified all 1,931,182 uncompressed artifact bytes and hashes after compressed transfer in 13.00 seconds.

Titanic D repeatedly searched source/group lists inside nested verification loops. Penguins B rebuilt its whole comparison tensor inside every pair check; that single execution interval was about 95 seconds. Numeric boundary conventions also differed between exploratory and packaged Penguins comparisons, requiring corrected evidence and another replay. Generic guidance now requires precomputed lookups, masks, text tokens and lengths, explicit boundary conventions and bounded comparisons for large data. It retains independent full-population assertions and does not supply dataset-specific thresholds or labels.

Dolly C searched repeatedly for a shared chat reader that is not installed in the minimal Daytona image, including a failed assumption that a namespace package has a file path. The SFT contract now explicitly names available local structural checks and the full validator enforced by platform publication. This removes misleading discovery work without skipping chat validation or source-to-output checks. Model-specific token limits remain downstream.

Titanic B lost the response to its fourth model request and became blocked with an unknown provider acknowledgement. The worker had not restarted or run out of memory during that call; the evidence does not identify the network cause. The uncertain request was never resubmitted. B was cancelled with its receipts/files retained. C, queued behind B, was also cancelled before making any model request. Both attempts and the known charges remain in the record; total B cost is unknown.

## Quality bar

The independent auditor checks retained original-file hashes and all landed values; every published feature/target or instruction/context/answer; shared training readers; complete lineage; exhaustive, disjoint role assignments; and exact input/declared-group separation. Titanic additionally checks all 704 label-blind near-match pairs under the same sensitivity rule as the baseline. The audit is outside the agent loop and does not provide corrective prompts or transformation code.

These checks establish data fidelity and consumer compatibility, not predictive quality. No model was trained. Titanic's historical outcome is not a causal decision, true passenger/family identity remains unverified, Penguins lacks individual-bird identifiers, and Dolly's observed responses are not independently fact-checked. Missing-context coverage and measured similarity limits remain in the retained handoff. The three datasets do not qualify arbitrary intents or large-data scalability.

Briefing cache checks returned identical verified facts on cold/warm reads: Titanic 0.11144/0.000803 seconds, Penguins 0.01410/0.000674 seconds, Dolly 0.03521/0.001083 seconds. Profiling itself is negligible relative to whole-run latency. The final trials use fresh sources, so these cache measurements are not presented as their speedup.

## Deployment note

The full retained checkpoint was 357,690,719 compressed bytes. Slow file-endpoint downloads were stopped before workspace creation. The existing verified checkpoint was reused, and 13,914,828 compressed bytes of changed files were transferred with size and SHA-256 checks. Reconstruction used the complete current manifest, preserving all current members, metadata and links and omitting removed members. The reconstructed archive was validated before restoration. The previous workspace remains retained for rollback. This operator upgrade overhead is separate from transformation latency.

## Every attempted run

All costs below are provider-reported model costs. Unknown acknowledgement charges remain unknown. Cancelled wait time is not pooled into successful-run comparisons.

| Trial      | State     | Completion/cancellation | Model calls |           Model cost | Independent audit |
| ---------- | --------- | ----------------------: | ----------: | -------------------: | ----------------- |
| dolly-a    | completed |               209.246 s |           9 |           $0.1186422 | Pass              |
| dolly-b    | completed |               170.089 s |          11 |           $0.1132523 | Pass              |
| dolly-c    | completed |               267.623 s |          13 |           $0.1421622 | Pass              |
| dolly-d    | completed |               131.051 s |           7 |           $0.0898338 | Pass              |
| penguins-a | completed |               150.692 s |           9 |           $0.1287689 | Pass              |
| penguins-b | completed |               268.317 s |          11 |           $0.1208928 | Pass              |
| penguins-c | completed |               120.552 s |           7 |           $0.0918365 | Pass              |
| penguins-d | completed |               143.253 s |           9 |           $0.1126783 | Pass              |
| titanic-a  | completed |               145.481 s |           9 |           $0.1108999 | Pass              |
| titanic-b  | cancelled |               483.748 s |           4 | Unknown; ≥$0.0519822 | Not completed     |
| titanic-c  | cancelled |               179.556 s |           0 |        No model call | Not completed     |
| titanic-d  | completed |               172.930 s |           7 |           $0.0885135 | Pass              |
| titanic-e  | completed |               128.766 s |           7 |           $0.0870281 | Pass              |
| titanic-f  | completed |               130.969 s |           8 |           $0.0967781 | Pass              |

Fourteen attempts: twelve completed and passed, two cancelled. Known model charges across all attempts total **at least $1.3532688**; one uncertain call prevents an exact total. Persistent Daytona and local infrastructure charges are excluded.

## Runtime, interfaces and retained evidence

Active workspace: `8e111e44-c441-4187-b535-b695428bd500`, always-running 4 CPU / 8 GiB / 10 GiB, private, with blocked outbound networking. Runtime: `sha256:c2ba39f456fbe6203f1963081aba8c29fcdfe1038b744e44f19c04931e86a9b5`. The previous workspace and checkpoint remain retained. Formatting checks did not change the deployed runtime identity.

MCP/public API impact: existing project-scoped task start/inspect/control and shared publication remain authoritative. The canonical SFT context now accurately names the validation boundary. No endpoint, database schema, generated client or Celery route changed. The native agent continues to execute local tools alongside its data and does not use MCP or browse webpages.

Evidence under `output/workshop-skills/`: `summary.json` and `summarize.py`; per-trial state, task, metrics and independent audit files; `measure.py` and `audit.py`; `runtime-qualification.json`; `briefing-cache-check.json`; `compressed-replay-check.log`; workspace upgrade/health receipts and local service health. `scripts/native-workshop.md`, the Data Workshop skill and the adjacent dataset documentation describe the implementation.

Final runtime-contract SHA-256: `5bfabbc4583c876d2845987af99fb138e674e32eeb9f26f64e025ed77e2253aa`. All three final runs report this same value. Rendered skill checksums are retained separately in the workspace health receipt.
