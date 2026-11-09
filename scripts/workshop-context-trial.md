# Native Workshop context and loop benchmark

Historical benchmark. See the [implementation audit](workshop-speed-implementation.md) for the current deployed configuration and the status of follow-up work.

The final unchanged revision completed three fresh Titanic trials in **97.873 seconds on average**, versus 124.333 seconds for the earlier optimized OpenRouter runs: **21.3% less elapsed time**. Mean provider-reported model cost was **$0.1065136**, versus $0.1019618: **4.5% higher**, or about half a cent more per run. All three passed the independent full-output audit, used seven model calls and needed no repair turns. The final Penguins decision and Dolly SFT regression cases also passed.

OpenRouter is the active route. The same `openai/gpt-6.1-sol` model uses standard/default serving through a reused HTTP client and streaming Responses endpoint. The former direct OpenAI WebSocket path was removed. No smaller model, reasoning-effort reduction, token/action budget, operator-authored transformation or corrective user prompt was introduced.

## Direct comparison

| Configuration                             | Titanic completion, mean | Model cost per run, mean | Runs |
| ----------------------------------------- | -----------------------: | -----------------------: | ---: |
| Earlier optimized OpenRouter              |                124.333 s |      $0.1019618 reported |    2 |
| Prior direct OpenAI WebSocket             |                121.384 s |     $0.1642033 estimated |    2 |
| Current OpenRouter + context/loop changes |                 97.873 s |      $0.1065136 reported |    3 |

The three current results are 92.834, 101.163 and 99.623 seconds. These are small historical comparisons, not randomized concurrent trials or a universal latency guarantee. The earlier OpenRouter transport used Chat Completions; the current route uses Responses. Transport, context and harness changed together, so these measurements do not isolate the causal contribution of each change.

| Final regression case          | Completion | Model cost | Result                                                |
| ------------------------------ | ---------: | ---------: | ----------------------------------------------------- |
| Penguins decision, 344 records |  106.513 s | $0.1056947 | Pass; seven calls, no repairs                         |
| Dolly SFT, 1,000 records       |  170.838 s | $0.1776719 | Pass; ten calls, one package repair and second replay |

Dolly is slower than its immediately preceding briefed run (116.160 s / $0.1451161). It corrected a source-type assumption during exploration, then discovered that script inputs omit platform-managed provenance. The generated ordinary audit metadata was incomplete; the agent retained an explicit source-file mapping, replayed and independently verified the repair. This cost is included, not treated as an outlier. There is no evidence of a universal speedup across intents.

## What changed

1. The first model request includes the exact pinned input manifest, measured source context and platform briefing after stable replay, consumer and taxonomy contracts. The same references remain local. The agent starts by examining data instead of rereading contracts or discovering the platform.
1. Duplicated consumer contracts and source profiles were removed from task context. Related exploration and notes share a Python execution. Installed readers, schema checks, return shapes, receipt envelopes and fingerprint verification have executable examples. Successful validation prints compact results rather than complete records or package source.
1. Missingness, prediction-time feature availability, leakage and grouping are assessed before packaging. Similarity guidance covers numeric boundary cases, missing features and sensitivity to nearby tolerances, without prescribing a Titanic-specific target or recipe.
1. Reference uploads and publication downloads are batched with full path, byte, hash and lineage verification. Native publication also checks row counts, partition counts and assignment digests against retained replay. `verified_against_replay` lets the agent finish directly after its independent semantic/value checks, without another receipt-reading call or duplicate presentation.
1. Markdown notes are permitted in replay packages. This removes a packaging failure without relaxing path, UTF-8, archive, runtime or execution constraints. Requested charts/tables still use the presentation tool.

## Final sequence and remaining time

Mean model-call time is 66.999 seconds (68.5% of completion); time outside model calls is 30.874 seconds. The earlier OpenRouter comparison spent 62.277 seconds in model calls and 62.056 outside them. The observed saving is therefore in orchestration and avoidable loop work; the model itself was not made faster.

All three final Titanic runs followed the same seven-call sequence: inspect source; investigate overlap/grouping; author and preflight the package; clean replay; independently check actual output values and splits; publish; finish. The first three code-generating calls plus independent verification remain real task work. They were not removed to improve the benchmark.

Mean source staging/setup is 10.146 seconds and publication is 5.557 seconds. Another 15.171 seconds remains across execution, replay, exchanges and lifecycle work; it is not all model-network overhead. The final handoff after publication averages 6.315 seconds. Existing response cache hits cover about 82.6% of cumulative input tokens in the final repeats.

The next measured opportunities are setup/transfer overhead, remaining exchange overhead and the size of retained tool observations. Further changes should preserve source verification, independent semantic checks and isolated replay. There is no evidence here for dropping those checks, choosing a smaller model or paying for a faster serving tier.

Found but did not change after the final benchmark: local preflight can be made to exercise the exact replay input shape, so assumptions about platform-managed provenance are caught before a full replay. Dolly demonstrates this remaining contract/usability gap. The deployed revision was kept fixed through all five final trials rather than changed after inspecting their results.

## Every measured attempt

Elapsed time is task creation through completion or the recorded interrupted/blocked state, including setup, model/tools, repairs, clean replay and publication. Upload and workspace-upgrade time are excluded. Model costs are provider-reported charges and exclude persistent Daytona and local infrastructure. Every iteration and failure is retained.

| Trial            |   Elapsed | Publication | Model time | Calls |                    Model cost | Independent audit                              |
| ---------------- | --------: | ----------: | ---------: | ----: | ----------------------------: | ---------------------------------------------- |
| titanic-a        |  101.451s |     89.454s |    72.750s |    10 |                    $0.1127187 | Core checks pass; near-match sensitivity fails |
| titanic-b        |  111.320s |     96.965s |    82.807s |    10 |                    $0.1204936 | Core checks pass; near-match sensitivity fails |
| titanic-c        |  131.814s |    114.923s |   100.528s |    13 |                    $0.1566347 | Pass                                           |
| titanic-d        |  130.729s |    118.990s |    98.790s |    13 |                    $0.1550143 | Pass                                           |
| titanic-e        |   92.350s |     82.573s |    64.605s |     9 |                    $0.0919915 | Pass                                           |
| penguins         |  101.943s |     78.300s |    75.808s |    11 |                    $0.1212449 | Pass                                           |
| dolly            |  146.429s |    127.377s |   111.146s |    13 |                    $0.1912226 | Pass                                           |
| titanic-f        |  107.935s |     99.943s |    76.049s |    11 |                    $0.1258097 | Pass                                           |
| titanic-g        |  115.524s |    103.983s |    85.078s |    13 |                    $0.1227656 | Core checks pass; near-match sensitivity fails |
| titanic-h        | 1257.660s |   1245.481s |    88.698s |    12 |                    $0.1204306 | Pass                                           |
| titanic-i        |  126.032s |    113.835s |    86.982s |    12 |                    $0.1245079 | Pass                                           |
| titanic-j        |   98.979s |     87.198s |    68.079s |     9 |                    $0.0977736 | Pass                                           |
| titanic-k        |  134.691s |    122.348s |    97.139s |    11 |                    $0.1276525 | Pass                                           |
| penguins-final   |  161.882s |    147.414s |   115.941s |    17 |                    $0.1895823 | Pass                                           |
| dolly-final      |  124.748s |    114.342s |    83.982s |    10 |                    $0.1428429 | Pass                                           |
| titanic-l        |  126.651s |    120.281s |    81.581s |     8 |                    $0.1501003 | Pass                                           |
| titanic-m        |  196.700s |     Unknown |    Unknown |     7 | Unknown (at least $0.1300162) | Interrupted; no publication                    |
| titanic-n        |  125.720s |    119.407s |    93.463s |     9 |                    $0.1655322 | Pass                                           |
| penguins-briefed |  118.077s |    111.893s |    81.725s |     9 |                    $0.1476079 | Pass                                           |
| dolly-briefed    |  116.160s |    108.118s |    79.485s |     8 |                    $0.1451161 | Pass                                           |
| titanic-o        |   92.834s |     87.138s |    65.559s |     7 |                    $0.1051198 | Pass                                           |
| titanic-p        |  101.163s |     95.442s |    69.692s |     7 |                    $0.1079914 | Pass                                           |
| titanic-q        |   99.623s |     92.095s |    65.747s |     7 |                    $0.1064296 | Pass                                           |
| penguins-compact |  106.513s |     99.344s |    75.494s |     7 |                    $0.1056947 | Pass                                           |
| dolly-compact    |  170.838s |    164.866s |   118.467s |    10 |                    $0.1776719 | Pass                                           |

## Iteration findings and retained failures

A–C supplied native contracts up front and batched transfers. A/B passed row/label/reader/lineage checks but failed the broader near-match sensitivity audit (87/130 cross-role pairs). C found and repaired overlap during its own verification. D/E removed duplicated context and documented readers/receipts; E passed in 92.350 seconds, but D spent three repair turns on ambiguous fingerprint semantics. E was not accepted as a representative speed result by itself.

F–H clarified record versus file-byte hashes, but F/G still rehashed an already-computed digest before repairing the mistake. G left ten broader near matches across roles. H encountered an external Daytona DNS failure, was fenced/paused, then resumed through the existing control API after receipt inspection and connectivity recovery. Its full 1257.660-second wall clock is retained, including the outage; no completed model action was resubmitted.

I–K added the exact fingerprint assertion and tolerance-sensitivity guidance. All passed, but averaged 119.901 seconds at $0.1166447: only 3.6% faster and 14.4% more expensive than the earlier OpenRouter mean. Two still needed repairs. This was not treated as a strong speed gain.

L/N and the briefed regression cases added the source briefing and native publication verification. They reduced the final handoff, but validation helper discovery and large return dumps inflated model context. M overlapped a local API/worker recreation and lost a model acknowledgement. It was blocked, inspected and cancelled without resubmitting the uncertain request. Its total cost/model time remain unknown, and its known charges remain in the table. This is an interrupted deployment trial, not an output-quality pass or an assumed provider fault. Subsequent service restarts were awaited to completion before starting more trials.

O–Q and the compact regression cases use one unchanged final contract: exact validation imports, return types and compact-output examples. No runtime or prompt changes were made between these five trials. The three Titanic repeats needed no repairs. All prior rejected or slower attempts remain visible; the final mean does not pool earlier revisions or subtract outages.

## Quality criteria and limits

Each retained original-file checksum and landed value is checked. Every output has source-bound lineage and passes the shared downstream training reader. Decision labels and every selected feature value are compared to the source; target/outcome proxies are excluded from the model request. Dolly preserves the exact instruction/context and assistant response in 1,000 SFT examples. No difficult rows or labels are silently dropped.

Partitions must cover the source exhaustively and have no overlapping identities, identical model inputs, or inherited/declared contamination groups across roles. Every observed decision class must appear in every role. Each final Titanic trial retains all 891 records; final Penguins retains all 344.

The extra Titanic sensitivity check covers 704 pairs with equal passenger class, sex, family counts and embarkation; fare distance at most 0.1; age distance at most one year or both missing; deck ignored. All final repeats have zero cross-role matches under this definition. This is a plausible sensitivity rule, not proof of duplicate passengers or true family identities. Historical optimized OpenRouter outputs have 119/108 cross-role matches under this stronger check. The final harness therefore does more overlap checking than that baseline.

No downstream model was trained or scored. Training-reader compatibility does not prove predictive quality. Split label proportions can differ when groups remain intact. True family independence, feature-acquisition timing, pretraining contamination and universal leakage safety remain unestablished. These three small datasets do not qualify every possible intent, PDF/image workflow or large-data workload.

The final Penguins grouping leaves 298 train, 24 development and 22 final records. All three species appear in every role, but those small held-out populations limit evaluation precision; the agent reported this limitation. Titanic final-role label proportions also differ from training. Passing the integrity audit does not certify statistical power or deployment suitability.

## Verification and deployment

The current runtime passed 32 focused tests, both existing work-once integration checks and all 14 live no-LLM qualification checks. The latter cover persistent execution, intentional failure/repair, recovery without duplicate mutation, isolated clean replay, exact nullable integers, inherited grouping, late follow-ups, answer-only completion, terminal exit and cancellation during a model wait (0.525 seconds).

Earlier iterations passed 42 transport/agent/registry tests, five resident-loop tests, 27 staging/provisioning tests, two work-once integration checks and one focused streaming-capacity check. Counts overlap; they are not a unique-test total. Required repository hooks passed. An accidentally broad Make test run was interrupted and replaced with a scoped passing run; the full suite was not run. No frontend change or frontend test is claimed.

Active Daytona workspace: `020ea2c6-9ac5-4fee-825e-97dd61426773`; always-running 4 CPU / 8 GiB / 10 GiB, private, with direct local data access. Runtime: `sha256:cc4baba20c7d8d3b87f523afb4ad94c50321e4dea0b9dd1ebb02692e3340ec99`. Prior workspace/checkpoint are retained. Final native-contract SHA-256: `ec1503c5e812a731112ed2040938af9e139a0a03323c2265e7630d82449bbb3b`.

MCP/public API impact: existing task start/inspect/control and shared lineage-bound publication remain authoritative. These are internal context/runtime changes; no generated-client change is required. The native agent uses local tools/data, with model I/O relayed by the backend; it does not call MCP or browse the Console.

## Evidence

`output/workshop-context/` contains all source-upload receipts, task snapshots, per-call usage/timings, audits, qualification and workspace upgrade receipts. `summary.json` holds aggregate measurements; `measure.py` reads retained action records; `audit.py` compares published outputs to original bytes and exercises shared training consumers.

Across 25 attempts, the retained provider charges total **at least $3.3419655**. The exact experiment total remains unknown because M has one unacknowledged call. Infrastructure charges are excluded.
