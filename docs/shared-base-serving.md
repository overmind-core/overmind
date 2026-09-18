# Shared-base LoRA serving

## Implementation

The production path is in `modal_shared/serving/{artifacts,weights,snapshot}.py` and `overbae/modal/modal_vllm_worker.py`. It has no dependency on the experiment package.

Each compatible base/serving profile owns reusable adapter-free engine snapshots and one BF16 runtime-layout weight artifact. Registration's existing `pre_warm` completes preparation before marking a deployment READY. Another adapter with the same profile joins that pool; it does not export another copy of the base.

Modal can create multiple hardware-specific snapshots for that profile. Its documentation says GPU functions generally need two or three snapshots per GPU type and may be recaptured for runtime/security updates. A successful pre-warm therefore does not guarantee every subsequent allocation is a snapshot hit. Report new snapshot construction separately from confirmed restores; do not promise uniformly low scale-from-zero latency. GPU snapshotting remains an alpha Modal feature. [Modal memory snapshot limitations and FAQ](https://modal.com/docs/guide/memory-snapshots).

Preparation loads the base, initializes vLLM and CUDA graphs, exports checksummed 2 GiB shards, caches parsed shard metadata, and enters level-2 sleep. Restore wakes weight allocations, streams the artifact through pinned host memory into the original GPU addresses, resets empty LoRA banks, wakes KV memory, and checks health. Tenant adapters attach only afterward.

The cache identity includes the sealed base revision, effective engine arguments (including context and LoRA rank), GPU, image, runtime versions, loader source, and tensor layout. Different profiles are not interchangeable. Updates invalidate compatibility rather than silently reusing old engine state. Source files are hashed at sealing; artifacts are hashed at creation/reuse during preparation. Restores validate the manifest and immutable file size/mtime identities, not a full checkpoint rehash.

All existing GPU/image pairs have shared-base worker classes; GPU selection is unchanged. Full-checkpoint workers remain non-snapshot. The existing registration policy excludes all MoE bases from automatic adapter serving because training targets expert layers. That policy is unchanged: the manually registered 35B attention-only fixtures validate the serving machinery, not automatic MoE training-to-deployment eligibility.

Both HTTP hops send immediate and 15-second keepalives while waiting: SSE comments for streams, leading whitespace for JSON. Late backend failures use an error object in the response body because headers have already been sent. The Django client rejects these JSON errors and forwards SSE incrementally, without a 512-byte accumulation delay. Public inference methods cannot forward internal sleep, reload, collective-RPC or adapter-control endpoints.

## Deployment order

Do not deploy the inference worker ahead of base sealing.

1. Deploy the updated registration app into the intended Modal environment.
1. Run its `fetch_base_model` for every base used by existing shared-base deployments and wait for success. This reuses existing weights, writes the content manifest once, and returns `base_identity`. Do not write or edit manifests manually.
1. Deploy the inference app from the same release. It creates the environment's `overmind-inference-artifacts` Volume. Deploy the Django API with the matching client and worker-stat changes.
1. Run the existing `pre_warm` for each deployed base/profile and adapter to be qualified. Wait for successful generation before enabling traffic. Preparation can take minutes; this is registration work, not the intended scale-from-zero request path.
1. Verify `shared_base_snapshot` and `shared_base_ready` logs, then force an idle worker down and verify a fresh runtime restores the same origin. Exercise adapter A, B, and A again before release approval.

No database migration or new public API/MCP contract is introduced. Existing registration and completion tools retain their contracts. This is an internal serving lifecycle and transport change.

Rollback by redeploying the preceding matched registration/inference/API release. Leave sealed base files, adapter directories, and immutable artifacts in place: the older non-snapshot workers do not need the new artifacts. Do not delete shared base weights to roll back. An artifact-integrity or restore error fails closed; investigate the logged cause and rebuild a corrected release rather than modifying a published generation.

## Docker validation

`scripts/validate_shared_base_serving.py` refuses non-dev environments. Run it inside the existing API container with `PYTHONPATH=/code`, a catalog model, two prepared adapter paths, rank, context, and rounds. It creates isolated dev fixtures and deactivates them afterward; it does not overwrite existing deployments or delete shared weights.

The script separates preparation from measured requests, terminates the prepared worker, then measures fresh runtimes through the authenticated Docker Django completion endpoint. Each round records:

- Request-to-ready estimate with a measured cross-host clock uncertainty.
- First content token latency, excluding keepalives and role-only chunks.
- Complete first and second request latency.
- A/B/A switching and an arithmetic canary through Docker HTTP; supplemental token-distribution checks through Docker-to-GPU RPC because the public API does not forward logprobs.
- Snapshot origin, unique runtime, artifact identity, restore duration, weight-reload duration, and loaded adapter identities.

These are end-to-end dev HTTP measurements, not isolated loader benchmarks or a production SLA. GPU allocation, gateway startup and network overhead remain in request latency. Three samples cannot establish a p95 or capacity guarantee.

## Measured results — 2026-09-18

Matched registration and inference releases were deployed to `overmind-dev`, not production. The live Docker tests passed arithmetic generation, fresh-runtime checks, shared artifact reuse, and A/B/A adapter switching. The two final 70B rounds also passed matched-cache A/A/B/B/A probability controls with zero measured within-adapter or recovery error. These serving checks do not establish all-model qualification or a cold-start SLA. Subsequent [fresh training-to-serving validation](fresh-lora-validation.md) found missing training cost accounting, inaccurate cold-request classification, and an unqualified Qwen training mask fallback; resolve those before claiming end-to-end production qualification.

All times below are seconds. Cold start means **HTTP request start to engine ready**, not just the restore hook. TTFT excludes keepalives. Request latency includes the complete response to the short arithmetic prompt, not a long generated answer.

| Model / scenario                           |    Cold start |   TTFT | First request | Second request |
| ------------------------------------------ | ------------: | -----: | ------------: | -------------: |
| Qwen3.5-35B-A3B / H200, confirmed restore  |  24.75 ± 0.16 |  26.01 |         26.17 |           2.43 |
| Qwen3.5-35B-A3B / H200, new snapshot build | 356.65 ± 0.22 | 357.52 |        357.56 |           2.00 |
| Llama-3.3-70B / B200, earlier restore      | Not collected | 111.91 |        112.05 |           2.00 |
| Llama-3.3-70B / B200, later restore 1      |  29.81 ± 0.25 |  30.90 |         31.03 |           1.89 |
| Llama-3.3-70B / B200, later restore 2      |  16.39 ± 0.26 |  17.41 |         17.43 |           2.83 |

Both profiles use BF16 and 32,768 context, with rank 16 for 35B and rank 8 for 70B. The 35B restore reloaded weights in 9.31s and finished its post-snapshot hook in 10.66s. The later 70B restores reloaded in 9.44s / 8.53s and finished the hook in 10.33s / 9.69s. The earlier 70B reload took 93.99s: storage/cache sensitivity remains, even on a confirmed snapshot hit. Exact values, runtime identities, outliers and canary evidence are in [the result artifact](shared-base-serving-results.json).

The final production-code `make test` run passed with 3,635 tests and eight skips. The subsequent benchmark-control changes passed nine focused tests. Scoped pre-commit checks passed. Live tests caught and corrected a roughly 61-second gateway idle disconnect and duplicate inherited Modal lifecycle hooks. GPU test workers were shut down; isolated dev test users, projects, tokens and model records were deactivated. Base weights and reusable artifacts were retained.

One timing sample was discarded after client wall/monotonic clocks diverged. Subsequent runs inhibit idle sleep for the benchmark's lifetime. A strict per-tail-logprob diagnostic rejected a 35B run; A-only controls reproduced the variation (probability difference 0.00006617 versus A/B difference 0.00143122, with exact A/B/A recovery).

The earlier 70B round similarly failed a cold-versus-warm probability comparison despite correct HTTP answers. Follow-up controls on that same worker showed A-only prefill variation and exact matched-cache recovery. The final diagnostic warms both adapters, then compares A/A/B/B/A probability mass. It retains the 0.001 maximum error and requires A/B separation greater than four times the measured control/recovery error (and greater than 0.000001). The final 70B separations were 0.02688 and 0.01100 with zero control/recovery error. This is a serving canary, not a dataset-level quality evaluation. No matched non-snapshot baseline was rerun, so these measurements do not establish a causal speedup ratio.

The 35B fixture uses synthetic attention-only LoRA adapters registered directly by the dev benchmark. It does not qualify real expert-targeted MoE training output or change the existing MoE merge policy. The 70B test uses separately republished archived dev training adapters, without changing their existing deployments. Live qualification covers these two GPU/image/model profiles, not every catalog model or GPU.

The existing Docker API needed its image rebuilt for a missing Starlette dependency. Its old dev database migration history failed the entrypoint's bootstrap, so that container uses the existing `RUN_DB_BOOTSTRAP=0` option; no migration records or schemas were changed. Production migration validation remains a separate release prerequisite. The image build also reported existing Truss dependency conflicts; these were not changed by this serving work.
