# Workshop batch and validation benchmark

## Method

Compared main `5cbca9b9` with the application implementation in `2f07b9af` on staging, using the same seven ordered AURA CSVs and unchanged retained `group.py` and `decision.py` scripts. The only package-manifest adjustment from the earlier production run was binding the existing staging sandbox runtime. No training, evaluation, inference or Modal jobs were launched.

The source contains 113,013 rows. Both imports produced source SHA-256 `35a1cbb86b3310ebad7958624d26b063598ddbe5e63d15e6d5102a60760bda89`. File order was CEAS_08, Nazario, Nazario_5, Nigerian_5, Nigerian_Fraud, SpamAssasin, TREC_07. The existing script's declared truncation and duplicate grouping were unchanged.

Each landing/interactive task retained 1 vCPU and 4 GiB. The existing CPU autoscaler changed interactive desired count from one to two during the baseline; a transformation executes on one worker and was not parallelized across those tasks. The benchmark did not change autoscaling limits, CPU, memory or provisioned disk capacity. Durable data remained on EFS; candidate temporary data used the task's existing ephemeral disk. These are observed paired-run timings, not a concurrency-capacity guarantee.

## Results

| Measurement                                           |  Baseline | Candidate |
| ----------------------------------------------------- | --------: | --------: |
| Source cells for seven files                          |         7 |         1 |
| Rows in cumulative source versions                    |   399,488 |   113,013 |
| Server-side landing execution                         | 202.983 s |  62.134 s |
| Import wall time, including transfer and polling      | 541.013 s | 313.287 s |
| Full two-step transformation                          | 537.252 s | 393.141 s |
| Combined server processing (landing + transformation) | 740.235 s | 455.275 s |
| Impact measurement across both steps                  | 196.939 s |  37.151 s |

Observed improvements: landing 69.4%, transformation 26.8%, combined server processing 38.5%, and impact measurement 81.1%. Publication/profile time did not improve in this pair (84.611 s versus 92.313 s); it remains a substantial CPU/scan cost. The result does not claim every phase became faster.

Transfer time is not treated as a server-performance result: the same bytes crossed the local network in both runs, and Wi-Fi/polling variation affects wall time. Landing execution comes from durable admission timestamps; transformation phases come from saved pipeline receipts.

Two exact-file local comparisons independently verified ordered row content, lineage, impact findings and full measurements (excluding timestamps and file identity). Mean validation/measurement time fell from 17.576 s to 11.404 s, about 35%. This local subset is separate from the full staging measurement.

## Changes

- One resumable transfer binds ordered file hashes, sizes and destination settings; incomplete or invalid batches publish no source version. Existing single-file clients use the same service.
- One SQLite source index is shared by lineage and impact checks for each step. Review flags are removed during the lineage write instead of another full-file rewrite.
- Known-schema writes stream directly to Parquet; statistics are collected during profiling. Publication measurements run before acquiring the dataset lock, with settings and cancellation rechecked before atomic publication.
- Staging landing/interactive tasks use local temporary storage. Each run cleans up its scratch directory, and file locks protect active runs while abandoned scratch is reclaimed.
- Validation receipts expose measured row counters and preserve runner heartbeat separately from forward progress.
- MCP upload guidance, SDK/plugin versions, generated API client and documentation were updated. The deployment renderer accepts AWS's normalized empty-host representation, preserving repeat deployment.

## Verification

- Backend: 3,311 passed, seven skipped.
- End-to-end journeys: 92 passed, including CLI batch retry, mixed workload capacity and recovery.
- SDK: 634 passed.
- Frontend: 905 tests passed; typecheck, Biome, design/contrast/control checks passed.
- Deployment renderer: 22 passed, including repeat deployment using AWS's normalized volume shape.
- Batch tests cover incomplete/bad members, one-cell attachment, file-order conflicts, lost acknowledgements and managed-upload isolation.
- Storage tests preserve the previous file on interrupted writes. Scratch tests retain active leases and reclaim abandoned directories.

Live MCP queries verified all 113,013 unique row identities, identical ordered transformed-content and lineage-key checksums, matching per-source label/truncation counts, correct target probability ordering for every row, and valid parent-cell/row bindings for every row. The final consumer schema check passed. An additional full-source run was cancelled during the first lineage check. It remained cancelled, preserved the same active version and all three published cells, and a subsequent 1,000-row preview completed. During that run, a separate project landed 100 actual AURA rows in 0.436 seconds of server execution (5.970 seconds including transfer and observation). Read-only MCP polling remained responsive during this probe. This is a bounded cross-project smoke check, not a general capacity bound.

One local polling request encountered a connection timeout during the candidate run. Observation resumed against the exact recorded job ID; no duplicate transformation was submitted. Server-side timings above come from the completed receipts, not local polling intervals.

Implementation review: https://github.com/overmind-core/overmind/pull/216. Companion docs: https://github.com/overmind-core/docs/pull/77. Production was not deployed.
