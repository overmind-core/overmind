# Native Workshop latency trial

Historical benchmark. See the [implementation audit](workshop-speed-implementation.md) for the current deployed configuration and the status of follow-up work.

The controlled source is `output/native-workshop/resident-titanic-original.csv`, SHA-256 `81787d320d7f7b03df935e91de8bd19e11d45c5bbcab86ef4d4a76dc91b7d4f2`. Each trial uses a fresh dataset, the objective “I want to train a decision model on this data”, and `openai/gpt-6.1-sol` through the existing OpenRouter endpoint. No effort, serving-tier, prompt, action-count or token limit changes were made.

## Baseline

Task `286d4e7a-3fdb-4935-9813-4b06e4f0963d`, dataset `09fda631-e76b-49e0-abed-00b673ffaf4c`, runtime `sha256:e5e17d2ff693e83f7fb429193f0d3a737575c486a68a1ba790f7ad6558f06f61`.

- Completion: 277.714 seconds from task creation to terminal update; no maintenance pause.
- Publication: 177.693 seconds.
- Model calls: 13; 69.793 seconds summed action duration; reported cost $0.126159.
- Seven Python executions: 0.972 seconds combined from kernel start/end receipts.
- 891 output records; 625 train, 133 development, 133 final.
- Independent checks: all observed targets match the source, seven selected feature values preserved, source identities/lineage preserved, no target/proxy in model inputs, disjoint source identities and exact model inputs across partitions. The shared training reader accepts the records. Family separation remains unverified because the source has no ticket/person identifiers. No model training was performed.

A direct transport probe measured 0.825 seconds to attach, 0.851 seconds importing the agent inside Daytona, 0.208 seconds for an empty Python process, and 2.073 seconds for a read-only agent exchange. The old supervisor also slept two seconds after every exchange, including delivery of a completed model response.

## Change under test

`workshop_runtime/mailbox.py` owns atomic receipts, control/inbox delivery and process liveness with standard-library imports only. Running exchanges wait at most one second locally for new work, returning immediately when it appears. The supervisor no longer sleeps while running. Paused tasks keep their bounded polling. Heavy model/data imports stay in the resident runner. Runtime identity includes the mailbox. Cancellation, request identity conflicts, uncertain provider outcomes and publication checks are unchanged. MCP and REST contracts are unchanged.

## Verification

34 focused tests passed, covering resident recovery, cancellation, retained Python state, publication and conversation handling; the mailbox CLI also works with site packages disabled. Two work-once integration tests passed. Repository hooks passed. Live recovery/replay qualification and optimized trials are recorded below after deployment.

## Measured results

| Trial            | Completion | Publication | Model calls | Model action time | Model cost |
| ---------------- | ---------- | ----------- | ----------- | ----------------- | ---------- |
| Baseline         | 277.714 s  | 177.693 s   | 13          | 69.793 s          | $0.126159  |
| Optimized        | 124.833 s  | 98.687 s    | 11          | 61.364 s          | $0.0997319 |
| Optimized repeat | 123.833 s  | 112.648 s   | 10          | 63.190 s          | $0.1041917 |

The optimized mean is 124.333 seconds: 55.2% lower elapsed time (2.23× throughput for serial comparable tasks). Mean model cost is $0.101962: 19.2% lower than the baseline. These are three measured runs of one dataset, not a general performance guarantee. Provider and model-output variability remain; the trials made different numbers of calls. Most of the observed gain is outside model calls: non-model elapsed time fell from 207.921 seconds to a mean of 62.056 seconds. The model itself was not made faster: median call duration was 3.264, 3.465 and 4.746 seconds respectively.

The optimized transport-only probe took 0.920, 0.779 and 0.591 seconds per exchange. Live no-LLM qualification passed runner termination/recovery without duplicate mutations, clean replay isolation, exact integer preservation, partition inheritance, late follow-ups and model-wait cancellation (1.008 seconds). The runtime is `sha256:f1cc5876da846f88ffec88cdc4c5ab4ccad8466e311251460d24f6d8919c9799`.

Both optimized outputs passed independent full-source audits: all 891 row identities and targets, all seven model-input feature values, target/proxy exclusion from model input, source lineage, shared training-reader validation, exhaustive/disjoint split identities and exact-input separation. The first optimized split is 625/133/133 train/development/final; the repeat is 624/133/134. No downstream model training or accuracy measurement was performed.

The repeat agent incorrectly invoked `decision_reference` with one argument during its independent check. It read the error, supplied the proper request/reference arguments and reran verification before publication. This was an agent-authored verification error, not a transport failure. Its recovery is included in the measured time and cost; no operator supplied a corrective prompt or edited the generated recipe.

### Model-call findings

| Trial     | Input tokens (including cache hits) | Cached input | Output tokens | Reasoning tokens (included in output) |
| --------- | ----------------------------------- | ------------ | ------------- | ------------------------------------- |
| Baseline  | 225,073                             | 199,510      | 4,232         | 104                                   |
| Optimized | 151,852                             | 132,434      | 3,796         | 137                                   |
| Repeat    | 136,103                             | 115,592      | 4,137         | 223                                   |

Caching was already effective (85–89% of input tokens); reducing reasoning effort is not the demonstrated opportunity in these runs. No premium serving tier, smaller model or additional token cap was introduced. Provider-reported model charges total $0.3300826 across these three trials. Daytona and infrastructure costs are excluded; the persistent workspace remains 4 CPU / 8 GiB / 10 GiB.

## Deployment and recovery

The old workspace `44933a6d-f84c-455f-ae0e-aa9c18c85136` is retained archived for rollback. The active workspace is `653ade8c-1b59-4a50-9a97-79d12c57e0ed`. A 319 MB checkpoint exposed whole-file memory copies in the upgrade command; validation, hashing and upload now read from the file rather than materializing the archive as bytes. The process was resumed with the same saved creation receipt, restored bytes were checksum-verified and archive-compared, and no duplicate replacement was allocated. The additional archive safety test passed. Local API and native worker were recreated with the new snapshot; API, frontend, Postgres, Redis and sandbox health checks passed. The unused intermediate image was removed.

Evidence is retained in `output/workshop-latency/`: task/publication receipts, independent audits, model usage, mailbox probes and the live runtime qualification report. The optimized task IDs are `f9d29b4c-8696-4992-8c8d-17905cb716c9` and `089de09c-8c8e-4f00-9989-3bdf09c2f738`.
