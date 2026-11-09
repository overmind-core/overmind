# Direct OpenAI Workshop trial

Historical benchmark. See the [implementation audit](workshop-speed-implementation.md) for the current deployed configuration and the status of follow-up work.

Direct OpenAI Responses WebSockets are deployed locally, using `gpt-6.1-sol`, `service_tier=default` and `store=false`. Each active task reuses one connection. The backend keeps the provider credential; Daytona has no provider key or network dependency. This trial does **not** establish a meaningful end-to-end speed improvement or a cost saving over the previous optimized gateway implementation.

## Controlled inputs

Every run starts from a fresh copy of `output/native-workshop/resident-titanic-original.csv`, SHA-256 `81787d320d7f7b03df935e91de8bd19e11d45c5bbcab86ef4d4a76dc91b7d4f2`, with the objective “I want to train a decision model on this data”. Source fingerprint: `ba1536581313d6a1eb629b4dad871676004c60b7dfad89daf3597d5311629a48`.

The model family, instructions, tools and reasoning-effort configuration were unchanged. No token budget, action limit or premium serving tier was introduced. The direct transport uses Responses instead of Chat Completions; this changes provider message handling, so these are complete-agent comparisons, not a controlled measurement of network overhead alone. Fresh tasks can choose different transformations and numbers of turns.

Time is task creation to terminal completion, including startup, exploration, repairs, replay, publication and final presentation. Source upload/landing and infrastructure upgrade time are outside that interval. Publication time is reported separately; it is not substituted for task completion.

## Results

| Configuration                                    | Completion | Publication | Model calls | Summed model-action time |   Model cost per run |
| ------------------------------------------------ | ---------: | ----------: | ----------: | -----------------------: | -------------------: |
| Previous gateway, run 1                          |  124.833 s |    98.687 s |          11 |                 61.364 s |  $0.0997319 reported |
| Previous gateway, run 2                          |  123.833 s |   112.648 s |          10 |                 63.190 s |  $0.1041917 reported |
| Direct WebSocket, before further harness changes |  137.944 s |   106.255 s |          14 |                 65.916 s | $0.1498807 estimated |
| Direct WebSocket + optimized harness             |  110.110 s |    91.191 s |          14 |                 70.893 s | $0.1479654 estimated |
| Direct WebSocket + optimized harness, repeat     |  132.659 s |   111.971 s |          17 |                 93.492 s | $0.1804412 estimated |

The final configuration averages **121.384 seconds and $0.1642033** across two runs, versus **124.333 seconds and $0.1019618** for the previous gateway configuration: 2.4% less elapsed time and 61.0% higher model cost. At the observed averages, 1,000 runs would be approximately $164.20 versus $101.96 in model charges. This is an extrapolation from two runs, not a usage guarantee. These results do not justify advertising direct WebSockets as a substantial end-to-end speedup.

The harness improvement is clear in the recorded breakdown: non-model elapsed time fell from 72.028 seconds in the first direct run to 39.217 and 39.167 seconds. The previous optimized gateway averaged 62.056 seconds outside model calls. The model did more work in the new trials, offsetting most of that gain.

Every live direct run used one socket, with incremental continuation on all subsequent turns (13, 13 and 16 continuations). All 45 calls reported the default serving tier. There were no live connection/context recoveries. Median time to the first provider event on subsequent calls was 0.249, 0.271 and 0.219 seconds; this is **not** time to the first readable answer token.

Direct costs are calculated from reported input, cached-input, cache-write and output tokens using the standard short-context rates: $2.00, $0.10, $2.50 and $10.00 respectively per million tokens. Cache writes are removed from ordinary input before charging, and reasoning tokens are already included in output. These are estimates, not invoice receipts. Rates were checked against [OpenAI pricing](https://developers.openai.com/api/docs/pricing) on 2026-11-08 UTC. The three direct trials total approximately $0.4782873 in model tokens. Daytona and local infrastructure charges are excluded; the persistent workspace remains 4 CPU / 8 GiB RAM / 10 GiB disk.

## Harness and recovery changes

- One task-scoped Responses WebSocket replaces per-call gateway requests. Subsequent turns send new input with `previous_response_id`.
- Full responses are retained before delivery to the resident agent. Provider phase, nested metadata and encrypted reasoning survive the Pydantic message round trip and coordinator replacement.
- A replacement connection sends full retained context. An explicit `previous_response_not_found` rejection before generation permits one full-context retry. An unknown in-flight acknowledgement is fenced, never automatically regenerated.
- Reference files upload/download in batches. Every expected path and byte sequence is verified before launch.
- The supervisor retains its validated Daytona transport, checking the live owner, sandbox ID and workspace generation before every exchange. A replacement supervisor validates a fresh attachment.
- Cancellation remains responsive while the provider is silent. Source hashing, independent replay, lineage reconstruction, partition checks and publication authority remain enforced.

The Responses transport follows [OpenAI WebSocket mode](https://developers.openai.com/api/docs/guides/websocket-mode). MCP/REST task contracts and the Console client did not change.

## Output quality

All three outputs passed independent full-source audits of all 891 records: original row identities, observed survival labels, selected feature values, exclusion of `alive`/`survived` from model state, source lineage, training-reader compatibility, exhaustive split membership and disjoint identities/exact inputs. Missing values remain unknown. No training run or downstream accuracy evaluation was performed.

| Trial            | Train | Development | Final | Selected inputs                                |
| ---------------- | ----: | ----------: | ----: | ---------------------------------------------- |
| First direct     |   624 |         135 |   132 | age, embarked, fare, parch, pclass, sex, sibsp |
| Optimized        |   625 |         134 |   132 | same, plus deck                                |
| Optimized repeat |   624 |         134 |   133 | age, embarked, fare, parch, pclass, sex, sibsp |

The final repeat initially failed replay because its declared numeric age column did not allow missing values. It corrected the schema and replayed. It also identified cross-split near-profile pairs, strengthened its grouping recipe and replayed again before publication. Independent verification confirmed all three declared grouping fields (`profile_group`, `party_proxy_group`, `near_profile_group`) are disjoint across the published partitions. The time and cost of these repairs are included above; no operator supplied a corrective prompt or edited its package.

The final repeat uses readable text prompts rather than JSON states. The independent audit was adapted to parse its seven explicit field labels and compare each value with source data, including unknown age/embarkation values. Audit-only fields are retained separately from the model state. Grouping by inferred party/profile keys does not prove family independence: this source lacks ticket and passenger identifiers. Prediction-time availability still needs domain review, particularly for the run retaining deck. Formatting and these audits do not establish downstream predictive quality or causal decision suitability.

## Verification and deployment

The focused agent/provisioning/conversation run passed 39 tests after the final harness changes. Earlier focused resident/model-registry checks passed, as did two work-once integration tests. Repository hooks passed on the changed implementation files. A real Daytona qualification with no LLM passed 14 execution/recovery checks, including runner death without duplicate mutation, clean replay isolation, exact nullable integers, inherited split groups, late follow-ups and cancellation while waiting for a model (0.527 seconds).

The broad capacity suite is **not qualified**. Attempts encountered an outdated test image/missing runtime mount, Docker memory exhaustion and a host-versus-Compose Postgres mismatch. The runtime mount was corrected; the full image build was interrupted during dependency downloads to free resources. Capacity testing needs an isolated, adequately sized test stack. It must not be reported as passing. Live trials ran against the actual Compose database after the affected services recovered. Final API `/health` and Console checks returned HTTP 200; API, frontend, Postgres, Redis and sandbox report healthy, and the native worker is running.

Active workspace: `9c981f27-fb68-4923-8569-dfb52f355a40`. Runtime: `sha256:fc29be775c51cfafd13e10df9ba03370ea2c080a41bf3087bdf574de30ea4403`. Snapshot: `overmind-workshop-fc29be775c51cfaf`. The prior workspace `653ade8c-1b59-4a50-9a97-79d12c57e0ed` is retained archived after checkpoint/restore verification.

## Evidence and remaining work

Evidence is in `output/workshop-websocket/`: `trial-{a,b,c}.json`, `audit-{a,b,c}.json`, `measure.py`, `audit.py`, `runtime-qualification.json` and `workspace-upgrade.json`. The previous results are in `scripts/workshop-latency-trial.md`.

| Trial        | Task                                   | Dataset                                |
| ------------ | -------------------------------------- | -------------------------------------- |
| First direct | `442e6056-567a-4aa6-bfc8-b88f8d460cb5` | `af1c45bc-2f5a-4fbf-a910-0c79d1eaf8f7` |
| Optimized    | `0178b278-f507-494b-a791-a386745c0449` | `293a6575-9134-4c72-bdf7-7001c884a0e1` |
| Repeat       | `1beb276b-89f3-4371-8b51-219861f6154d` | `57b67183-c897-43b1-a89f-beea53858d0c` |

The remaining performance bottleneck is agent turn count and generated context: these runs made 14–17 model calls versus 10–11 previously. Further work should reduce reference-discovery and unnecessary presentation turns while preserving independent verification and useful repairs. The current results do not support cutting exploration, replay or contamination checks to achieve a timing target. A larger dataset/intent evaluation and isolated capacity qualification remain outstanding.
