# MCP-only preparation → training → serving experience audit

Status: real training, activation, inference and rollback exercised; six targeted defects repaired and live MCP retests recorded. Product qualification remains incomplete. Started 2026-10-08 PDT / 2026-10-09 UTC.

## Scope and authorization

Real localhost Overmind MCP, existing workers and real provider execution. User-workflow operations use MCP, with no browser, direct REST, database mutation or shell upload. A subsequent, explicitly separated repair phase uses repository tools, isolated regression tests, read-only worker/provider diagnostics and runtime deployment. Those activities are not presented as MCP user-workflow evidence.

The user authorized up to $100 total and explicitly requested activation and real inference. Start with a bounded 8-step, 300-second single-GPU attempt. The platform does not expose an all-in provider spending cap; leave substantial headroom for preparation, startup, serving and unreported components. Do not describe partial receipts as an invoice.

Project: financial-services (`e18b29b5-915d-45a7-80cd-77ffe6559205`). Original dataset `KYCMCP Test` (`c4cf07c3-17ff-44c7-8d5a-94832751f1d8`) is not rewritten. Test datasets and recipes are explicitly named `MCP UX audit`.

Original preparation: `5f55e973-c042-48ba-a85c-850619a6ddb5` (10,000 rows, Qwen3.5-27B). Observe only; do not submit a duplicate.

## Observed failures and friction

### Incorrect source lineage survives successful transformation

The source cell `6d1b5eda-fdf0-4a34-9d20-c8261f092972` has 10,000 distinct file-row references, but every uploaded row also contains `_overmind_parent_rows: [0]`. The existing conversation transformation produces 10,000 rows with only one distinct file-row reference and one declared parent. Its impact receipt nevertheless says `identity_preserved: true`, `rows_added: 10000`, and `rows_removed: 10000`.

Reproduced through MCP on a separate derived chain: dataset `24d403c9-5968-5025-a22c-ee83ebd07045`, source `1e361636-f1c0-4940-beae-7121b74a08e0`, conversation run `29da2f79-6323-4bf2-8b6a-128306f3c674`, output `0d6286d4-28b8-470b-ae06-e13489a0bfc2`. Both a normal conversation and a select excluding the uploaded parent column still collapse file-row lineage. This is an interaction between malformed caller-authored metadata and platform interpretation, not evidence that the original labels changed.

Downstream impact: partition `6d7b927d-c373-46f6-950d-5083409386f9` fails with `Too few independent content/group clusters for the selected roles`. It recommends retrying the unchanged recipe, although a deterministic lineage problem will not be repaired by retrying it.

Confirmed through ordinary Training readiness as well: enabling a 10% validation split returns `All rows belong to one content/group cluster; an independent holdout cannot be created.` This safeguard is working; disabling validation would bypass the protection, not fix lineage. The top-level recovery instruction is misleading legacy copy: `fix the rows the validator lists, then run the notebook again`, despite a corpus-level error and the retired notebook replay workflow.

MCP-only recovery on test data: query 128 complete rows from the original source, explicitly bind each output to its actual `source_row`, and import unchanged question/answer pairs as messages. Output `7d07f168-ad8c-4a3c-ab95-670687303a46` has 128 distinct file rows and parents; zero answers changed. This is an audited workaround, not a production-code fix.

### Low development loss does not match served output behavior

The second model, Qwen2.5-0.5B-Instruct, reports development loss 0.003575 and a succeeded training job. All 16 held-out requests complete technically, but none returns the requested raw JSON with a boolean `match`. Responses contain Markdown code fences and an object in `match`, even after removing fences. This is a failed output-contract check, not 16 successful user outcomes.

To distinguish a purely held-out issue, query the exact development cell and replay three examples (pair indices 753, 2085 and 2661). All three also fail the target schema. Only original user messages are sent; references are withheld from inference. This establishes a concerning training-to-serving discrepancy, not its cause. The successful job's `artifact_identity`, `reload_verification` and artifact `inference_contract` are null; MCP does not provide evidence proving which exact checkpoint/adapter was loaded. Do not claim that a missing adapter is proven, or hide this failure by parsing arbitrary free text into a decision.

### Preparation progress cannot establish liveness

The original preparation repeatedly reports `tokenizing`, 0/10,000 rows, no error, while the outer job `updated_at` advances and the inner progress timestamp remains fixed. These timestamps describe different things but are not labelled accordingly. The connected receipt does not supply actionable stale-progress interpretation or cancellation.

The new 112-row preparation also reports `running` with `uploading` and all 112 export rows complete before tokenization reports arrive. Export count is not transfer completion or preparation completion.

### Different observation tools return different essential facts

For ready preparation `34691dca-d776-418c-8a1a-f93ae8deddf6`, `get_job` omits `tokens`, `supervised_tokens` and `tokenizer_revision`. Repeating the exact `prepare_training_data` call returns the same receipt plus 161,711 tokens, 1,792 supervised tokens and maximum length 5,677. An agent following the advertised generic job-status path cannot explain the same detail as one repeating the preparation action.

For a warming deployment, `get_job(kind=deployment)` returns `progress: null` and `updated_at: null`; its linked deployment resource exposes the stage, retry fields and deadline. A running/deploying training job's `updated_at` was also observed equal to its creation time despite later stage transitions. Timestamp semantics and observation surfaces are not consistent enough to infer liveness generically.

### An explicit preparation setting is silently changed

Negative preparation `610201f3-a6fb-49cc-8db0-ebbff6622eb7` was requested with `context_length: 128`. The accepted configuration and final report instead use 4096, without a requested/effective change warning. It reports six incompatible rows out of 96, with concrete row references and reasons such as `5673 tokens exceeds context length 4096`. This establishes silent normalization of an explicit setting; it is not a valid test of a 128-token limit. The platform should reject an unsupported explicit value or obtain acceptance of its replacement.

### Immediate cancellation can be ineffective and misleading

Run `be049c6e-27f0-4f93-aa84-80935c2c08e6` was queued, then immediately followed by `cancel_dataset`. Cancellation took approximately 3.61 seconds; the run completed and published its 10,000-row output in approximately 3.56 seconds. The response still said `Cancellation requested`, with no explicit already-completed outcome. This observation does not prove publication occurred after an acknowledged cancellation; it establishes that the immediate cancellation attempt did not prevent publication and the response did not explain the race.

### MCP-only lifecycle gaps

- A new file/PDF source cannot be landed using the connected MCP tools alone; the upload resource requires CLI/REST transport. The audit does not silently take that route.
- An empty draft recommends `inspect_dataset_workbench` to attach source data, but that tool cannot attach bytes. Querying the empty draft returns `cell_not_found`, not an actionable no-source message.
- No connected tool lists standalone training preparations or ordinary training jobs by dataset. Exact receipts are needed to recover them; saved experiments have a listing tool.
- No connected tool cancels training/preparation, deletes or archives test datasets, or stops a deployment. This constrains recovery, cleanup and budget control from MCP alone.
- `run_inference` targets a deployment; it does not by itself prove the application's capability alias is connected. Activation, serving smoke calls and application traffic must remain distinct evidence.

### Cold original-model rollback took over thirteen minutes

Rollback to original deployment `4e93c921-54b7-4279-8996-059810bcc8c5` was requested at 02:37:56 UTC. At 02:45:04 UTC the activation still reported `verifying`; the first test model remained selected, as expected while target health was unverified. The target deployment reported `ready`, while its worker reported `warming`, two queued inputs, zero running inputs and one runner.

A single direct inference health check on the original model timed out at the MCP client after 120 seconds. The tool returned a transport error, not a durable request ID or a domain-specific receipt. Subsequent metrics showed zero requests and no failure, so execution/completion remained unknown; the request was not replayed. Do not equate nominal ready status, a worker count or pending activation with successful rollback. This also exposes a host/server timeout mismatch and an inability to reconcile an outstanding inference through an exact MCP receipt.

Rollback subsequently completed at 02:51:12 UTC. The final capability read after repairs confirms the original deployment selected and activation complete. The timed-out inference remains distinct from that successful activation receipt.

### The earlier data-preparation handoff did not retain the original file in Workshop

The original chat named `sample_10000.json`. The dataset inventory contains one different file, `sample_10000_kyc_pairs.jsonl` (36,487,432 bytes), and its saved `brief` is empty. The JSONL already contains authored prompts and answers. This does not establish retention of the original JSON bytes or its top-level metadata within Workshop. The original local file is not claimed lost. Separate the earlier native-agent handoff failure from platform transformation behavior: the current audit starts from an already-landed projection and cannot certify a fresh raw-file ingest through MCP alone.

### Response quality and decision friction

- Dataset inspection clips core consumer instructions into incomplete phrases even though cells/sources are paginated. `truncated_fields` can be empty while nested sections say `truncated: true`.
- Workbench/job receipts repeat large row examples; one two-run workbench response serialized to roughly 136,000 characters including text and structured copies. These are envelope characters, not measured network bytes or token counts.
- Completed exploration and partition jobs continue suggesting `get_job`; the next useful consumer action is absent. Terminal `completed_at` is null in observed completed workflow receipts.
- Readiness says `Fine-tuning is ready` while exact preparation is incomplete and quality is unmeasured. The warnings remain visible; the summary does not distinguish launch eligibility from preparation or quality.
- Premature inference and activation are correctly rejected, but both errors say `retryable: false` and provide no followable deployment-status action despite the transient deploying state.
- A deployment in top-level `warming` state reports worker `state: unknown` and `warming: false`. The different meanings are not explained in the response.
- While deploying, the first LoRA resource advertised `quantization: fp8`; once ready it correctly reported `bf16`. This is misleading provisional metadata, not evidence that FP8 weights were actually served.
- The same bounded chat recipe receives a 602-second/$0.6604 ordinary estimate, while the experiment forecast reports unknown duration/total and the GPU attempt is configured for 300 seconds. The estimate does not communicate how its assumptions relate to the explicit runtime bound.
- Installed model-workflow guidance still mentions interface contract 2.0, a Workshop agent and proposal review; the connected contract is 3.0 and those legacy workflow instructions are not authoritative.

## Verified working boundaries

- Project scoping rejects a valid preparation ID in the wrong project without exposing it.
- A changed pipeline under the same request key conflicts; wrong source fingerprints are rejected.
- Replaying derivation and pipeline execution returns their existing IDs.
- Replaying experiment launch retains the saved experiment; inspect child identities to confirm no duplicates.
- Read-only dataset queries reject DELETE; invalid partition fractions and unknown training models produce explicit errors.
- Whole-source exploration counts all 10,000 rows and exposes paged sampling allocations. Full row queries retained tested long strings exactly; sample inspection clipping must not be mistaken for stored-data loss.
- A missing-column pipeline fails with its concrete column name. Previously published versions remain addressable.
- A nonexistent imported parent fails without publishing an output cell. A deliberately missing assistant target remains readable as a Workshop cell but fails Training readiness; selecting the previous valid version restores 128 usable examples.
- Explicit-parent import preserves the selected 128 rows and their existing answers; the subsequent split succeeds with 96 train / 16 development / 16 final and reported exact-content/declared-group overlap zero.
- Repeating an already-complete activation leaves the selected model and completed receipt unchanged. The response summary still says activation requested and routing will change after verification, rather than stating that the requested model is already active.
- Inference rejects empty messages at validation and rejects a deployment belonging to another project without exposing its state.

## Product assessment

The user-facing goal is a usable model, not a sequence of green tool responses. The current journey can train and serve real outputs, but it requires the agent to reconstruct essential facts across inconsistent observations and work around source-lineage defects. More conversational explanation cannot compensate for missing platform facts.

| Concern               | Evidence                                                                          | User consequence                                                          | Required product behavior                                                                                |
| --------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Data correctness      | All-parent collapse survives two deterministic transforms                         | Independent holdouts fail; transformation success overstates preservation | Distinguish landed user fields from authoritative lineage; validate lineage contracts before publication |
| Progress and recovery | Long upload/tokenization/warming periods have no measurable intermediate movement | User cannot distinguish slow work from a stall                            | Consistent stage timestamps, measured units, last forward progress, deadline and actionable recovery     |
| Exact settings        | Requested context 128 becomes 4096 silently                                       | User cannot trust experiment intent                                       | Reject unsupported explicit values or expose a change requiring acceptance                               |
| Cancellation          | Immediate cancellation returns after successful publication                       | User cannot tell whether work was actually prevented                      | Receipt that explicitly distinguishes canceled, already completed, and in-flight work                    |
| Model handoff         | Activated dataset-first model rejected by swap prompt                             | Training success does not reliably produce application instructions       | Support explicit capability selection or a valid data-first model endpoint handoff                       |
| Inference errors      | Oversized request yields generic retryable server error                           | Agent may repeat an invalid request                                       | Actionable context-budget validation with input/output limits                                            |
| Cost control          | Partial worker estimates; no MCP stop-deployment or all-in cap                    | Agent cannot enforce a precise ceiling from platform facts alone          | Measured cost coverage, lifecycle stop controls and bounded execution enforcement                        |
| Source transport      | Fresh PDF/file bytes require non-MCP transfer                                     | Strict MCP-only file-to-model journey is incomplete                       | Explicit, verifiable transfer contract; do not imply MCP alone lands bytes                               |

Utility should be measured as a lineage-correct dataset and a usable, traceable model, including recovery effort and wall-clock time. Product delight here means clear and reliable evidence, not more generated commentary. This audit does not write analytics events or claim a product-delight score.

## Actual paid journey

Balanced 128-row test sample: 64 per existing judgement, selected by row number within each judgement ordered by `md5(CAST(pair_index AS VARCHAR) || ':42')`. Retrieve via eight 16-row MCP queries; compare every question's returned length with server-side `length(question)`. This is a functional test sample, not a population-representative quality benchmark.

Partition: `ce333c97-ef02-4853-ada8-e56a68ba735a`.

| Role        | Dataset                                | Cell                                   | Rows |
| ----------- | -------------------------------------- | -------------------------------------- | ---- |
| train       | `73223cd7-4921-5c14-a924-aa5638421154` | `707a562b-d9bb-4db9-94d5-f879e2f7a3ce` | 96   |
| development | `a099ce3c-a6c4-5251-b335-734018449214` | `3c269c52-7294-435b-8b1a-69b714d57910` | 16   |
| final       | `85a9abf9-aa69-5ccd-941b-d3475dbea67f` | `f0afd6b7-4cbd-4078-92b9-abde59148b30` | 16   |

Training model: `Qwen/Qwen3.5-0.8B`, LoRA rank 8/alpha 16, batch 1, requested context 32768, learning rate 0.0002, 8-step / 300-second runtime profile. No automatic before/after chat evaluation jobs. Held-out inference is separate from training loss.

- Preparation `34691dca-d776-418c-8a1a-f93ae8deddf6`: ready, 112 rows, 0 incompatible, 161,711 tokens, 1,792 supervised tokens, maximum 5,677. Provider worker measured 23.56 seconds, CPU-only; this excludes orchestration/startup gaps.
- Experiment `39899f89-912a-473e-978a-11e5f0c3d082`: launched.
- Training job `2a04ad10-266d-44f2-9ea1-2a9795a28b39`: completed all eight optimizer steps and entered deployment. Training loss decreased from 1.387243 to 0.159437; development loss was 0.141546. These are not evidence of held-out KYC quality. Observed training compute estimate is approximately $0.1712 plus preparation approximately $0.0029; all-in invoiced spend is unknown.
- Deployment `adc3fb75-861b-4908-b390-55f6525dfc8e`: ready at 02:31:11 UTC, approximately 12 minutes 22 seconds after creation. Several minutes of startup exposed no detailed forward progress. Ready metadata reports BF16 on L4.
- Activation requested at 02:33:19 UTC and completed at 02:33:56 UTC. The capability resource confirms the active model changed from `4e93c921-54b7-4279-8996-059810bcc8c5` to the test model, preserving the former as `previous_active_model`. The same capability-level activation ID was reused; an old ID is not an immutable record of an earlier switch.
- All 16 held-out inference calls completed, returned parseable JSON, and stopped normally. Label agreement was 13/16 (81.25%); wrong pairs were 4874, 6988 and 9898, all false positives. No foundation baseline was run; this does not establish improvement or production suitability. Prompts ranged from 1,055 to 9,558 characters and retained full text. Platform end-to-end median was 1,428 ms, p95 7,124 ms; first cold request was 8,832 ms. The 16 responses used 14,754 total tokens and recorded an estimated $0.009116.
- A one-output-token request returned `finish_reason: length`, `truncated: true`, and partial JSON rather than claiming a complete answer. An explicit 20,000-output-token request against the published 16,384-token serving context returned generic `inference_failed`/`retryable: true`, with no context-budget detail; deployment metrics recorded only `server_error`. No large response was generated.
- The application handoff tool failed after successful activation: `get_model_swap_prompt` returned `This training run is not linked to a capability and its project has several.` The dataset-first training flow and later activation do not supply the association this handoff requires. Application-request timestamps remain null: MCP deployment smoke calls are not proof of application alias traffic.

The original 10,000-row preparation also reached ready: maximum 21,851 tokens, no incompatible rows, approximately 709.57 seconds of measured CPU worker time. It was observed at zero committed rows before its terminal update. The source-lineage problem remains independent of technical tokenization success.

Repeating that exact preparation action reused the original ID and returned the otherwise omitted totals: 10,933,068 tokens and 160,000 supervised tokens.

A second compact-model journey uses the same 96/16 pinned train/development cells, Qwen/Qwen2.5-0.5B-Instruct, context 8192, LoRA rank 8/alpha 16, eight steps and a 300-second attempt limit. Experiment `75337d1e-c466-4fc5-9eeb-1495b64509f5`, job `ca4e4bf8-36d0-40f1-a9b3-eb1ec732484b`, preparation `b1cecdf8-123f-48d5-bf60-1f054f26e37a`, deployment `48f5db06-9568-4f0a-ba12-0df387942c6f`. All eight steps completed; development loss was 0.003575 and observed training compute estimate approximately $0.0611, plus preparation approximately $0.0030. Deployment became ready at 02:37:42 UTC, approximately 8 minutes 21 seconds after creation. All 16 held-out calls returned text, but 0/16 met the target schema. Three additional development probes also failed that schema. The first 16 used 16,319 tokens, median end-to-end latency 1,501 ms, p95 5,899 ms, and an estimated $0.007601. The second model was not activated; the first model's activation/rollback is the routing test.

Structured call evidence is stored in `mcp-prep-training-ux-audit-2026-10-08.json.gz` (calls 1–119) and `mcp-prep-training-ux-audit-2026-10-08-continuation.json.gz` (later calls and per-example inference assessments). Source prompts/answers and later generated row content are omitted, while pinned identities, parameters and observed outcomes remain. Both JSON files passed parsing validation with `jq -e '.calls | length'`.

Both training experiments subsequently report `completed` with exactly one succeeded child each. Their completed receipts still suggest `get_job` and say to prepare a forecast before launch, despite retaining a forecast and having already finished. This is stale next-action/readiness copy, not unfinished training.

The two new jobs' training/preparation estimates plus their 36 completed inference responses total $0.25756035 in known components. This excludes the original 10,000-row preparation, the negative preparation, deployment startup/idle, the original-model rollback warm-up, storage/network and other unreported components. All-in actual cost remains unknown; no claim is made that the platform enforces the $100 authorization as a hard cap.

## Repeatability

Use the connected MCP interface/catalog, the explicit project above, and the pinned source IDs. Do not use a browser or direct HTTP to bypass a missing operation. Read each returned receipt with `get_job`; preserve request keys for retries of the same operation. For an independent new audit, use new clearly scoped request keys. Replaying this audit's keys must reuse its artifacts rather than launching additional work. Paid launch requires a reviewed recipe and remaining user-authorized budget.

The source-lineage reproduction uses `derive_dataset`, `save_dataset_pipeline` with one `conversation` step (`question=question`, `answer=answer`), `run_dataset_pipeline`, then `query_dataset` counting distinct `json_extract_string(_overmind_provenance, '$.file.row')`. Compare source versus output, not just row count and success status.

To repeat serving checks without retraining, query the pinned final cell for `pair_index`, `json_extract_string(messages, '$[0].content') AS prompt`, and `json_extract_string(messages, '$[1].content') AS reference`, ordered by `pair_index`. Send only each prompt through `run_inference` to either test deployment with temperature 0 and max_tokens 256. Require raw JSON, boolean `match`, string `decision_basis`, a normal finish reason and no truncation; only then compare `match` to the withheld reference. For development discrepancy reproduction, use the pinned development cell and pair indices 753, 2085 and 2661. Inference can incur costs and is not an application alias request.

Coverage boundaries: existing 10,000-row source, a balanced 128-row functional sample, two small chat model families, bounded training, selected safety/negative cases, activation, inference, and completed cold rollback. Fresh PDF sizes/counts, initial local-byte transfer, checkpoint download, application-authenticated alias traffic, training on all 10,000 rows, broad concurrency/soak/load testing, typed decision models and every catalog operation were not tested in this run. The test datasets and model artifacts remain because the connected MCP catalogue has no cleanup/stop tools. No original dataset cell was rewritten; all three original fingerprints and its active cell were rechecked unchanged.

This is a real black-box workflow audit, not an assertion that every MCP tool, dataset scale, model family or failure mode has been exercised. The observations above describe the initial journey; the following section identifies subsequent repairs. Other findings remain unresolved, including the second model's output contract, application handoff, cold-request reconciliation and cancellation messaging.

## Targeted repair follow-up

After recording the black-box observations, implementation inspection began in the same checkout. Product workflow calls and verification remain on MCP; repository inspection and changes are repair work, not substitutes for passing those workflows.

Rollback completed at 02:51:12 UTC, 13 minutes 16 seconds after the request. MCP confirmed the original deployment selected at 02:52:06 UTC. The first test model is no longer selected.

Before changing the error path, added an isolated transport regression for a failure the previous HTTP-400 mock missed: the real gateway has already committed HTTP 200 keepalive headers when its worker rejects context. Failure modes are lost error classification, raw-provider-detail leakage, non-stream JSON corruption, SSE termination, and the client treating a structured body error as success. The existing live MCP oversized-request observation remains the end-to-end retest; the focused regression covers the intermediate framing boundary that the current network mock bypassed.

### Fixes and real MCP verification

MCP impact classification: **MCP-ready**. These changes correct shared execution and error behavior and add observation facts within existing response fields; no new tool, route, input schema or legacy compatibility path is introduced. Repository guidance is updated. No UI or generated REST-client change was needed.

| Repair                                                                                                      | Live verification                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Reject unsupported explicit preparation context instead of rounding                                         | The same 128-token request now returns `preparation_invalid`, non-retryable, suggesting supported 4096; no new preparation receipt is returned.                                                                                                                                                                                                                                                                                            |
| Preserve numeric token measurements through credential redaction                                            | Original ready 10,000-row preparation now exposes 10,933,068 tokens and 160,000 supervised tokens through `get_job`. Numeric guards still exclude strings, containers, booleans and nonfinite values.                                                                                                                                                                                                                                      |
| Expose deployment progress and actual status-change time                                                    | The test deployment's `get_job` now includes ready stage, attempt, deadline and `updated_at=02:31:11.877121Z`. It does not invent a heartbeat.                                                                                                                                                                                                                                                                                             |
| Expose activation scheduling facts                                                                          | The completed rollback returns deadline `03:27:56.479293Z` and `next_poll_at=null`, alongside its actual completion time.                                                                                                                                                                                                                                                                                                                  |
| Preserve context-error identity across gateway keepalives and reject impossible output reservations locally | The 20,000-output-token request now returns non-retryable `context_length_exceeded` in approximately 3.3 seconds. A separate 28,030-character synthetic input with a valid individual 16,000-token reservation exercises worker-side total-context rejection and returns the same classified error in 19.183 seconds. No response truncation or silent budget reduction is used.                                                           |
| Do not execute landed parent metadata as new lineage instructions                                           | The original saved conversation recipe rerun on the pinned 10,000-row audit source publishes cell `90832af8-e776-46e4-9857-1fbffee7d4f9`, run `bbd5784c-9896-4f42-b217-b5feed453077`. Whole-source SQL confirms 10,000 unique row identities, file references and parents, and zero changed questions/answers. Receipt reports zero rows added/removed and 5.668 seconds of execution. Source bytes and original dataset remain unchanged. |

The lineage fix applies to new declarative pipeline runs. It does not rewrite historical incorrectly-derived cells. External imports still accept and validate explicitly supplied parent declarations. A paired integration regression covers both conversation and select operations; existing import/split/merge tests remain enabled.

The intermediate context-error fix initially mishandled non-object SSE frames. A failing regression was added before correcting that caller; null, list, numeric and `[DONE]` frames now pass through without a classification crash. This correction did not change the shared remote runtime package.

### Regression and deployment evidence

Environment: the current checkout, its existing Python environment and offline Django test settings; live user-workflow verification uses the running local MCP with the financial-services project. No test framework was substituted for the real paid journeys above.

Final regression command:

```sh
uv --cache-dir /tmp/overmind-mcp-audit-uv-cache run --no-sync pytest tests/test_modal_stream_keepalive.py tests/test_mcp_inference.py tests/test_inference.py tests/test_inference_routing.py tests/test_completions_api.py tests/test_mcp_resources.py tests/test_training_preparation.py tests/test_model_activation.py tests/test_workshop_redesign.py tests/test_workshop_mcp_acceptance.py -q 2>&1 | tee /tmp/overmind-mcp-final-regression-after.log
```

Observed result: **277 passed in 10.39 seconds**, with 163 warnings about the offline test JWT signing-key length. This is the named regression set, not the entire repository suite. Earlier expanded execution caught two routing fixtures that omitted an output limit despite defining contexts no larger than the 8192-token default; those fixtures now request 128 output tokens. The production default remains unchanged and impossible reservations remain rejected.

Before-fix failure logs: `/tmp/overmind-mcp-context-before.log`, `/tmp/overmind-mcp-context-stream-before.log`, `/tmp/overmind-mcp-facts-before.log`, `/tmp/overmind-mcp-observation-before.log`, `/tmp/overmind-mcp-lineage-before.log`, `/tmp/overmind-mcp-context-nonobject-before.log`. Intermediate passing runs included 107 preparation/observation/activation checks and 24 Workshop integration checks. One attempted command named nonexistent test files and ran no tests; it is not counted as passing coverage.

Successful provider runtime deployments (no training job launched by these commands):

```sh
uv --cache-dir /tmp/overmind-mcp-audit-uv-cache run --no-sync modal deploy overbae/modal/modal_vllm_worker.py --env overmind-dev
uv --cache-dir /tmp/overmind-mcp-audit-uv-cache run --no-sync modal deploy overbae/modal/modal_sft_worker.py --env overmind-dev
uv --cache-dir /tmp/overmind-mcp-audit-uv-cache run --no-sync modal deploy overbae/modal/modal_decision_evaluation.py --env overmind-dev
```

The inference gateway deployed successfully. Because the shared-file change participates in runtime identity, the matching SFT app `overmind-sft-12bf18c26c9bea26766574ac` and decision-evaluation app `overmind-decision-eval-95360419eb60723ed6c45206` were also deployed. No new GPU training or evaluation was launched after these repairs.

`pre-commit` was initially absent from the shell PATH, but is available through `uv run --no-sync`. All hooks passed on the 14 touched Python files and three skill documents after resolving one nested-if lint finding. `git diff --check` also passed for the scoped files. Evidence-file checks are recorded separately below.

### Remaining diagnosis and interrupted-run limitation

Read-only provider metadata confirms the second test adapter exists under `.adapters/ft-ca4e4bf8-qwen2-5-0-5b-instruct`, declares the expected `unsloth/Qwen2.5-0.5B-Instruct` base and rank 8/alpha 16, and carries a template consistent with the serving base tokenizer. The serving base has no standalone `chat_template.jinja`; its template is embedded in `tokenizer_config.json`. This narrows a template-mismatch hypothesis but does not prove checkpoint reload parity or adapter application to each served request. The malformed-output failure remains unresolved. No target labels or prompts were changed to manufacture a pass.

Post-fix 10,000-row partition `5e6f214a-4011-4a2a-b7c3-1464b2201dde` was interrupted while the development workers auto-restarted during further code edits. Its receipt remained `verifying, 0/10000`; a read-only worker inspection found no active tasks. The reconciler's expired-run interval is 1260 seconds. This is not valid steady-state dataset performance evidence and was not marked complete or reset through a backend shortcut. A separately identified seed-43 partition `df8c84cc-c6b5-4cc5-9c8c-ec9f1305d948` tests the stable-worker path with edits paused; its outcome follows below.

The stable-worker partition completed in **4.226 seconds** (creation `03:14:43.208751Z`, terminal update `03:14:47.435028Z`). It retained all 10,000 rows: 8,000 train, 1,000 development, 1,000 final; zero duplicates removed and reported exact-content/declared-group overlap zero. Positive/negative strata were 6152/1848, 769/231 and 769/231 respectively. Near duplicates remain unchecked and pretraining overlap unknown. The previously collapsed lineage no longer prevents an independent split.

Pinned outputs: train dataset `f3bad976-a383-5289-8f40-80a819a06d06`, cell `1d11a1f1-886c-4179-b725-6131c649803a`; development dataset `059ce800-253c-5839-b98f-072684fbb0ed`, cell `b0eceb84-3832-4d3d-863b-714d0c6e34f8`; final dataset `3bc4165b-27df-51ed-a84d-6dfc6852796d`, cell `228bd320-450f-4536-88d5-2c9c8531f2eb`. These are additional audit fixtures; they do not replace the original dataset or the 128-row model-test split. The interrupted seed-42 receipt remains unresolved in this observation window, with no paid work attached.

Final evidence checks: both JSON artifacts parse successfully and retain 243 logged calls (119 initial, 103 continuation, 21 repair follow-ups), spanning 25 named Overmind operations plus resource reads. This is not coverage of all 61 catalog tools. All applicable pre-commit hooks passed for the report and both JSON files, including JSON parsing and private-key detection. Raw test logs remain local under the explicit `/tmp` paths above; the saved evidence records commands, fixtures, identities and observed outcomes without source prompts or generated answer bodies.

Release judgment: **do not describe the product as fully verified**. The repaired 10,000-row transformation/split and context/progress paths have concrete passing evidence. The second chat model's output contract, missing checkpoint-to-serving parity evidence, data-first application handoff, cold inference request reconciliation, misleading cancellation recovery, MCP lifecycle stop/cleanup controls and all-in spending visibility remain unresolved. PDF batch/size coverage and an application-key alias request are explicitly outside this run's completed coverage. Both paid training jobs are terminal, and no further training or evaluation is queued by this audit. The interrupted partition is local CPU work; outcomes of previously timed-out inference requests are not reconciled by exact request ID.

The JSON receipts are gzip-compressed to stay within the repository added-file limit.
Use `gzip -dc tests/evidence/mcp-prep-training-ux-audit-2026-10-08.json.gz`
and the corresponding continuation filename to inspect them. The original JSON
files remain ignored local artifacts; compression preserves their exact bytes.
