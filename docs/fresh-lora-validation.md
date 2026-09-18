# Fresh LoRA validation — 2026-09-18

Fresh two-step LoRAs were trained through an isolated Docker API/Celery/PostgreSQL/Redis stack and the normal Modal `overmind-dev` training and registration services. Existing dev database/queues were not modified. Each job used 16 arithmetic examples, rank 8, alpha 16 and context 32,768. Training used H100 QLoRA; serving used a shared BF16 base. This is a lifecycle smoke test, not training-quality validation or a latency SLA.

Existing base weights, inference artifacts and snapshots were retained. After registration, the test made one user request, stopped the prepared GPU container, then measured a cold first request and a warm second request. “First open” here means an inference response, not browser rendering. TTFT excludes SSE keepalives and role-only chunks.

| Measurement                                    | Llama-3.3-70B / B200 | Qwen3-32B / H200 |
| ---------------------------------------------- | -------------------: | ---------------: |
| Training worker, including loading/saving      |              220.88s |          877.61s |
| Optimizer training                             |                4.80s |            4.59s |
| Submission through READY, observed             |              323.73s |        1,585.12s |
| First full response after registration/prewarm |                9.53s |            6.82s |
| Forced-cold request to engine ready            |       61.01s ± 0.32s |  171.34s ± 0.37s |
| Forced-cold first content token                |               62.22s |          196.89s |
| Forced-cold complete response                  |               62.32s |          197.03s |
| Warm second complete response                  |                1.57s |            6.40s |

The 70B run passed: all three responses were `391`, the restored runtime was new, only the requested adapter was loaded, and the existing snapshot origin and weight artifact were reused. Weight reload took 45.64s of the 62.32s cold request. Its GPU was stopped after the test.

The 32B run also passed all three arithmetic responses, fresh-runtime, exact adapter isolation and snapshot reuse checks. Weight reload took 132.85s, and the complete restore hook took 133.97s. About 37.37s preceded that hook; another 25.55s elapsed between engine readiness and the first content token. Those remaining intervals include infrastructure, routing and request processing; this test does not isolate their individual causes. Its GPU was stopped afterward.

The 32B training initialization spent most of its time before the optimizer; a stack sample was in Transformers safetensors tensor materialization. Registration prewarm took about 647.35s. During that interval, Modal listed the worker as Pending even though an H200 was allocated and vLLM was running. Pending alone must not be interpreted as GPU allocation delay.

## Release qualifications

- Both successful jobs left `FinetuningJob.cost_usd` null. Null is missing accounting, not free training.
- Both forced-cold requests were recorded as warm because recent traffic existed. For 70B, stored latency was 242.3ms versus 62.318s externally measured; for 32B, 15,409.8ms versus 197.033s. The recent-request heuristic is not reliable proof of actual GPU cold state.
- Qwen3-32B training used `multi_header_fallback` masking for all 16 examples; the 70B run used native generation markers. Successful arithmetic generation does not qualify Qwen's training mask correctness.
- These observations do not establish all-model readiness. Resolve accounting/measurement gaps and qualify the Qwen training template before claiming end-to-end production qualification. No product code was changed during this validation.

## Cost

The provider report retrieved after both tests totals **$3.20234639** across the dev training, inference and registration apps for the 11:00–13:00 UTC reporting window: $1.39945 training, $1.78114 inference and $0.02176 registration. This is provisional app/hour aggregation with reporting delay, not a per-job invoice or a finalized total. Platform-recorded training costs are missing.

Raw completed-run evidence is in [the result artifact](fresh-lora-validation-results.json). Modal's dev container list was empty after the runs. All five isolated Docker services were stopped and verified exited. Test users, projects and tokens were deactivated and test deployments marked deleted. Test Docker volumes, base weights, adapters and reusable inference artifacts are retained. No production deployment or product-code edit was performed, and the product test suite was not rerun for this operational check.
