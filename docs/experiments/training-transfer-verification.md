# Shared Modal training transfer verification

> Historical experiment record. Current implementation and qualification are tracked in [the platform verification report](platform-improvements/verification-plan.md). Earlier launch holds, runtime limitations and estimates below describe their recorded point in time. The authorized full run is `a356c75d-788a-4a35-bd30-1d24ae7afab8`; its existing completion protocol remains authoritative. New platform changes are isolated and have not been deployed over it.

## Design

All Modal chat and native decision runs upload preparation source rows as gzip JSONL, verified by checksum and streamed by the preprocessing worker. After exact preprocessing, the common runner transfers ordered binary SHA-256 row keys (32 bytes per selected occurrence). The worker checks the caller-pinned token artifact, each selection checksum and row count, then reconstructs the exact training and validation token files through the existing bounded SQLite index. Target probabilities, weights, tool schemas and message contents remain part of row identity. Duplicates and row order are retained.

This is full-data staging followed by reuse, not training on a partial dataset. Faster initial upload and avoiding redundant upload are complementary. Training checkpoints retain progress against the same fixed dataset; they do not trigger dataset transfers. For a corpus too large for available storage, separately designed shards and background prefetching may be appropriate. This change does not implement sharding or alter checkpoint behavior.

Modal's [Volume guide](https://modal.com/docs/guide/volumes) describes write-once/read-many storage. Its [large dataset ingestion guide](https://modal.com/docs/guide/dataset-ingestion) recommends downloading and transforming large datasets in Modal functions. A future cloud-source ingestion path can use that design without routing bytes through the local application host.

## Failure coverage and environment

Checks were written before implementation. Initial collection failed on the missing selection writer. Provider-boundary workflow fixtures use the real common runner, local copy of the volume, worker materialization and SQLite-backed test database. Only network transfer, base fetching and GPU allocation are replaced by local fixtures.

Fixtures cover both objectives, separate validation, duplicate training rows, Unicode, blank decision evidence, different soft targets and weights, structured tools, corrupt selection digests, truncated keys, wrong counts, unknown keys, changed artifact identity and preserving previous outputs on a failed validation selection. Existing preprocessing checks exercise compressed source reading, exact tokenizer output and failure reporting. Initial preparation and selection construction remain streaming.

Run from the repository root with the dev and test dependency groups installed:

```sh
uv run pytest tests/test_training_artifact_streaming.py tests/test_training_transfer.py tests/test_sft_preparation_process.py tests/test_training_preparation.py -q --no-cov --tb=short
uv run python scripts/measure_training_transfer.py INPUT_JSONL RESULT_JSON
```

No provider credentials or GPU job are required for these checks. The measurement command writes temporary local compressed/selection files, verifies a byte-identical gzip round trip and writes a JSON receipt. It does not transmit the dataset.

## Observed result

On 2026-10-02 Pacific, the focused suite passed 64 checks in 16.43 seconds. Two expected Modal warnings note that the worker function is running locally; the fixture supplies its volume. Pre-commit checks passed for all implementation, helper, test and playbook files.

Saved 8,320-row real-data fixture:

| Transfer                      |      Bytes | Reduction from raw |
| ----------------------------- | ---------: | -----------------: |
| Raw JSONL                     | 17,971,861 |                  — |
| Initial gzip upload           |  6,336,791 |             64.74% |
| Subsequent training selection |    266,240 |             98.52% |

Compression took 0.104 seconds and selection construction 0.104 seconds on the local host. These are fixture measurements, not network throughput guarantees. The source SHA-256 was `7deb96a2e7140b76577280f575be13262fa2892f0293af998876f34023a5ab03`. Larger or smaller source rows will have different reduction ratios.

Experiment-local receipts are in the chat's `jev-training-setup/transfer-improvement` directory: `verification-plan.md`, `tests-before.log`, `tests-after.log`, `tests-fixture-fix.log`, `tests-final.log` and `measured-transfer.json`. Some pre-existing training fixtures had implicit pending intent after the Workshop change; those now declare train explicitly without weakening runtime validation.

## Surface and deployment impact

REST, Console and MCP launches already converge on `run_finetuning` and `ModalRunner`, so all receive the change. MCP classification: MCP-ready through the existing shared service. No API schema, public tool, UI, database, Celery topology, seed data or generated-client changes are needed. The recovery qualification helper and sibling training documentation were updated.

The implementation is in an isolated attached worktree. It has not been deployed to the live Modal service. Compression and materialization code participate in preparation/training fingerprints, and the worker function's upload arguments change. Apply the platform changes and deploy `overbae/modal/modal_sft_worker.py` together during a window without in-flight submissions or training calls requiring their existing retry image. Confirm the intended Modal environment. Reusing an old artifact with a different fingerprint is not allowed.

The active full experiment remains on its original qualified code and existing submission. Do not redeploy its training worker while it still requires checkpoint continuation. No extra training job or model activation was used to validate this transfer improvement.
