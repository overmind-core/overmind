# Inference Engineering PDF: native SFT trial

The native agent ingested the original PDF and published a reproducible, technically valid 26-example SFT seed. It did not produce a comprehensive training corpus. Independent review found one incomplete citation set that the agent's final verification missed. No model training or evaluation was launched.

## Inputs and execution

- Original: `Inference Engineering.pdf`, Philip Kiely, 259 pages, 24,145,990 bytes. SHA-256: `b297183c38ffc7ffa30100e1aab0af3b1c20b49778939ee976522fe39ac0df89`.
- Minimal intent: “I want to prepare an SFT dataset from this PDF to train an assistant on inference engineering.” No example count, supplied answers or transformation recipe.
- Local project: `1e3f3e92-b50d-4590-85ed-97921d132d3c`.
- Dataset: `2f215885-e18c-482c-87b9-e0b43b400b3a`; source cell: `f4a3dfe0-18a0-4d7c-a23b-706c7aa4e3a7`.
- Native task: `30e478d5-0d68-4f98-a719-03c69b25c3cf`; model route: `openai/gpt-6.1-sol` through the existing OpenRouter endpoint.
- Daytona workspace: `c521c785-7904-4e81-b0e8-037e13ee9fe9`; runtime: `sha256:9ef0b54c87114a515d9ab905d01ba6dd55388537a2b888744342cf8c630f7f18`.

The agent read locally staged source data and platform references, explored the document, recorded a plan, authored questions and answers, retained a two-step package, repaired replay failures, inspected outputs and published. It used Python and local files, with no native-agent MCP calls, browser dependency or package installation. A missing `rg` command was handled with existing tools.

The source was uploaded afresh; an older 50,108-example dataset was not reused. A local API disconnect interrupted the upload observer, but the original import completed once. Observation resumed without a duplicate upload.

## Actual transformation

Landing produced **6,489 text fragments** on **240 pages**: 5,898 native PDF fragments and 591 OCR fragments. Nineteen pages had no landed rows; an independent PDF text reader also found no embedded text on those pages. Visual comparisons covered PDF pages 2, 31, 66 and 101. This does not establish perfect table, figure or OCR extraction across the entire book. A control character on a contents-page fragment remains preserved in the source audit, outside the selected evidence.

The first recipe step preserves all 6,489 source fragments, adding selection disposition and example usage. The second creates **26 standalone user/assistant conversations**, using **447 distinct evidence fragments from 30 PDF pages**, across 20 topic labels. The remaining 6,042 fragments remain in the audit; the agent does not assert that they are unusable.

Topics include roofline analysis, prefill/decode, batching, quantization, speculative decoding, prefix caching, parallelism, autoscaling and deployment. Answers contain 30–66 words, with a median of 49. There are no multi-turn examples, tool-call traces or comprehensive practical coding tasks.

Each published row includes `messages`, topic, source pages, exact evidence text hashes, document identity and inherited lineage. The actual Training reader consumes only `messages`; evidence and review metadata do not become question text or supervised output. The assistant responses are explicitly marked as agent-authored paraphrases, not observed expert answers.

All examples derive from one document family. The agent published an unsplit seed rather than claiming an independent holdout from random rows of the same book. There is no validation or evaluation result.

## Verification and limitations

The full published output and audit were exported and checked independently:

- All 6,489 audit rows preserve source text, page, filename and identity, with correct parents and selection accounting.
- All 26 conversations match the retained authored examples. Questions and answers are nonempty and unique; answer text is absent from the question.
- Every declared evidence hash and source page matches its source fragment. Final-to-audit and audit-to-source parent cells and fingerprints match.
- The unchanged retained package reproduced both step output hashes in a clean Daytona replay, using **zero model calls**.
- Overmind Training returned `ready: true`, technical `pass`, conversational format and 26 examples. It also returned task suitability **unmeasured** and **“No current workshop quality review.”** Validation and automatic evaluations were explicitly disabled for this inspection.

The operator read all 26 answers against their recorded evidence. No unsupported claims were identified in 25 during that bounded source review. **Example `ie-022` has an incomplete citation set:** its explanation that continuous batching replaces completed requests is supported by source rows 4852–4853 on PDF page 190, but those rows are absent from its evidence and lineage. Its selected nearby fragments establish token-level batching without fully supporting that part of the answer. Hash validity therefore does not establish semantic completeness. The original native output has been retained without an undisclosed manual correction.

This review is not an external expert assessment or a measurement of trained-model quality. The output is a qualified seed, not a finished corpus. Coverage is narrow, and independent evaluation is still absent.

## Failures and changes

An unrelated task that had finished by requesting clarification retained the project's only workspace slot. The coordinator now releases that slot only after a successful finish event, observed runner exit, no pending requests and no uncertain actions. Its task and files remain retained. The waiting PDF task then proceeded under its original identity.

The agent encountered four unsuccessful replay calls: two invalid attempts to represent train-only partitions, followed by two reads of platform-managed metadata that replay intentionally omits. It corrected these autonomously and succeeded on the fifth replay call. The shared workspace guidance now explicitly documents optional partitions, positive role fractions and the distinction between ordinary data and managed metadata. These guidance changes apply to future tasks; no improvement in a fresh model run has yet been measured.

The queue regression failed before the fix. The five-case regression passed afterward, including active, pending, uncertain and unfinished-task protections; the existing focused tests also passed. The Data Workshop journey, lint and pre-commit checks passed. No API or MCP contract changed.

The required load check is not passing. An initial invocation used an older test image without `workshop_runtime` and failed at setup. With the current development image, dataset scale, streaming and read-load checks passed, but the mixed-capacity wrapper failed. Its child checks reported failed pipeline completion in mixed/isolation scenarios, a recovery timeout and a missing `finished_at` receipt in landing-burst inspection. These findings were not repaired as part of this PDF trial. Evidence: `tests/journeys/.runs/20261107T053032Z/mixed-capacity.log`; parent result: three passed, one failed. No general capacity or recovery claim follows from the successful PDF replay.

The trial used **25 model turns and 848,393 reported tokens** for 26 examples. This is not an efficient throughput result. Large repeated context and repair turns are visible in the receipts; their individual cost contributions have not been isolated. The next improvement should be a coverage plan with compact evidence retrieval and an explicit claim-to-evidence review before publication. Merely requesting more examples would not resolve the missed citation or establish coverage.

Recorded task submission to completion was **13 minutes 17 seconds**. From the first model request to completion was **8 minutes 24 seconds**; the preceding queue/workspace setup interval was **4 minutes 53 seconds**, including the slot-release investigation. These intervals exclude source upload/landing and subsequent independent operator review. The total token count includes repeated input context; it is not a generated-output token count or a measured dollar cost. Cross-dataset timestamps and duration definitions are retained in `output/workshop-benchmark/timings.json`.

## Retained results

- Final cell: `512a1b92-cb87-4681-ba79-455a5662f49a`.
- Final fingerprint: `8a940732373aac12d436008b2226a7adad3ec8c5202a9b98e0423c45c7320d8e`.
- Pipeline: `0076d293-6a2f-4d98-94d5-f08bc53da4e6`; run: `3389ccc7-c999-4160-b94e-b8e9ee8e291a`.
- Retained replay package SHA-256: `54dfa7285ed0f452e15043e7aa364b5be27dfa42f635f0cb3e5bd6cf43a7227c`.
- Local evidence: `output/workshop-inference-sft/`, including source and audit exports, all 25 agent receipts, retained recipe, published rows, training-only messages, independent audit, replay report and Training readiness response.

[Open the published SFT seed](http://localhost:5173/datasets/2f215885-e18c-482c-87b9-e0b43b400b3a?projectId=1e3f3e92-b50d-4590-85ed-97921d132d3c&cell=512a1b92-cb87-4681-ba79-455a5662f49a).
