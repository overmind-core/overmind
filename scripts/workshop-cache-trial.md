# Workshop retention and recovery trial

Local Overmind project `1e3f3e92-b50d-4590-85ed-97921d132d3c`, qualified on 2026-11-08 (Pacific). No model calls, training, evaluation or inference jobs were started.

## Verified behaviour

- Settled task files are copied into project-scoped, content-addressed Overmind storage. The database retains the verified manifest pointer. Eviction is forbidden until both platform chunks and the current workspace inventory verify.
- The inactive cache defaults to 2 GiB per project. Least recently accessed retained copies leave first. Active, paused, uncertain, unretained and unowned directories remain pinned; this is not a hard cap on all sandbox disk usage.
- Recovery stages files, checks hashes, restores permissions and internal links, then installs the complete directory atomically. Cleanup also renames atomically before deletion, so an interrupted deletion cannot expose a partial workspace.
- Transfers use bounded batches and 32 MiB chunks. Existing verified source staging and all transformation/consumer/lineage checks remain unchanged. Retention happens during idle-project maintenance in the existing native worker, outside the model loop.

## Live results

| Workspace                | Entries |     Bytes | Eviction | Cold recovery | Exact inventory |
| ------------------------ | ------: | --------: | -------: | ------------: | --------------- |
| Cancelled Titanic trial  |      18 |   175,875 |   1.83 s |        6.65 s | Match           |
| Completed Penguins trial |     175 | 5,789,057 |   1.54 s |        8.77 s | Match           |

The cancelled Titanic already-loaded check took 2.46 s and required no file upload. The final completed-Titanic check took 5.36 s while backfill was running; initial retention took 6.41 s. These are single observations, not percentile guarantees. They measure the first qualified restoration path; subsequent batching and cleanup hardening retain the same verification contract.

The restored Penguins package replayed in 1.75 s with all 344 rows. Every step-output hash and partition-assignment hash matched the original receipt. Its runtime pin was updated to the currently installed qualified runtime; transformation code, parameters and partition recipe were unchanged. The new package and replay are scheduled for retention too.

A live Daytona subprocess holding the task directory correctly prevented inventory/cleanup. No files were removed. A local fault-injection test verified that a deletion failure hides the partial directory from recovery and can be cleaned up on retry.

## Checks

- 41 focused cache/agent/resident tests passed; one Linux-only process test was skipped on macOS and qualified separately on Daytona.
- Both work-once journeys passed.
- Model/migration checks passed. The branch migration check reports no committed migrations because this work remains uncommitted.
- Historical-directory audit: 140 directories, 111 matching task records, 107 eligible settled tasks and 29 directories without matching records. Unknown directories remain untouched.

Live Python memory and uncheckpointed active changes are not backed up against total sandbox loss. This implementation protects cache cleanup and verified retained snapshots; it does not claim zero recovery-point loss for running code. Platform storage and its database manifests need coordinated backups.

## Initial deployment

The worker was restarted locally with automatic retention enabled. At the measured checkpoint, 24 tasks (1,067,642,986 source bytes) had verified Overmind snapshots and 16 local copies had been evicted. Verified resident cache usage was 281,978,437 bytes; Daytona had 1,070,448,640 free bytes. Historical backfill continues in the worker. Unretained and unmatched directories remain pinned until safely reconciled.

Low-disk cleanup can evict verified cold copies before the cache reaches 2 GiB, reserving the smaller of 1 GiB or 10% of disk capacity. It does not remove active or unverified data to satisfy that reserve. Transfer batches travel as one archive, avoiding the bulk-file endpoint's per-file overhead.
