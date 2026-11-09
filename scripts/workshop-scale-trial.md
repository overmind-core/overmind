# Native Workshop scale trial

Historical benchmark. See the [implementation audit](workshop-speed-implementation.md) for the current deployed configuration and the status of follow-up work.

## Method

Two public Hugging Face corpora, nested shuffled subsets of 1,000, 10,000, 50,000, 100,000 and 200,000 real records. Seed 20261108; no duplicated padding. Exact revisions, source hashes and selected ordinals are retained under output/workshop-scale. Each case starts a fresh dataset and native task in the existing persistent Daytona workspace. Model and serving route remain OpenRouter openai/gpt-6.1-sol, standard pricing, with no agent step/token budget.

- [Covertype](https://huggingface.co/datasets/mstz/covertype): 54 measurement/indicator features and seven observed cover classes; [UCI original](https://archive.ics.uci.edu/dataset/31/covertype), CC BY 4.0. The pinned mirror's 52-column header is malformed. All 581,012 data rows match the original UCI bytes exactly; only the header was replaced with the documented 55-column schema.
- [Orca Math](https://huggingface.co/datasets/microsoft/orca-math-word-problems-200k): 200,035 synthetic question/answer pairs, MIT. Lossless Parquet-to-JSONL transport; text is unchanged. Formatting these records does not establish mathematical correctness.

Minimal intents: ‘I want to train a decision model to predict forest cover type from these measurements.’ and ‘I want to fine-tune an assistant to answer maths questions.’

The independent audit reads every row, verifies original upload bytes and landed values, walks lineage, checks transformed inputs/targets with downstream readers, and, where partitions are produced, proves complete/disjoint membership with no exact-input or declared-group overlap. Training-only output is reported separately; missing holdouts are not scored as zero overlap. It uses SQLite to avoid holding the entire audit population in memory. Semantic correctness, geographic independence and near-duplicate recall remain separate from these mechanical checks.

## Measurements

Task time includes preparation, model calls, local execution, clean replay and publication. Upload/landing is measured separately. Source download and the external audit are excluded. A dagger marks a recovered task whose elapsed time includes failed attempts and operator repair; it is not an uninterrupted latency measurement. Model cost is actual provider-reported usage, not an estimate; workspace and storage charges are not allocated per task. Anonymous memory is the maximum observed at 20-second intervals across the workspace, not an exact per-process peak. File cache is excluded. One run per cell measures capacity, not statistically stable latency.

| Case             | Input MB | Upload s |   Task s | Model s | Calls | Model USD | Audit             | Sampled anon GiB |
| ---------------- | -------: | -------: | -------: | ------: | ----: | --------: | ----------------- | ---------------: |
| covertype-1000   |     0.13 |      4.5 |    111.3 |    76.9 |     8 |   $0.1180 | pass              |             0.80 |
| orca-1000        |     1.21 |      4.5 |    125.9 |    75.8 |     8 |   $0.1008 | pass              |             0.92 |
| covertype-10000  |     1.29 |      7.7 |    121.6 |    70.7 |     7 |   $0.1075 | pass              |             1.26 |
| orca-10000       |    11.74 |      5.1 |    194.0 |    84.6 |     8 |   $0.1087 | pass              |             1.25 |
| covertype-50000  |     6.47 |     10.8 |    181.8 |    70.4 |     7 |   $0.1068 | pass              |             2.21 |
| orca-50000       |    58.73 |      8.2 |    524.3 |    76.2 |     7 |   $0.0984 | pass              |             2.76 |
| covertype-100000 |    12.94 |     23.6 |    281.3 |    73.7 |     7 |   $0.1111 | pass              |             3.07 |
| orca-100000      |   117.66 |      8.7 |    745.2 |   119.2 |    14 |   $0.1435 | pass              |             4.86 |
| covertype-200000 |    25.88 |     78.3 | 1930.1 † |   103.4 |     7 |   $0.1161 | pass              |             3.84 |
| orca-200000      |   235.42 |     34.1 |   1196.5 |    75.7 |     8 |   $0.0896 | pass; no holdouts |             4.54 |

| Case             | Prepare s | Briefing s (within prepare) | Model s | Publish s |
| ---------------- | --------: | --------------------------: | ------: | --------: |
| covertype-1000   |       9.9 |                         0.1 |    76.9 |       4.8 |
| orca-1000        |      13.0 |                         0.0 |    75.8 |       5.3 |
| covertype-10000  |      12.7 |                         0.9 |    70.7 |      12.1 |
| orca-10000       |      56.6 |                         0.1 |    84.6 |       9.3 |
| covertype-50000  |      24.0 |                         2.4 |    70.4 |      37.2 |
| orca-50000       |     193.8 |                         0.6 |    76.2 |      43.8 |
| covertype-100000 |      42.5 |                         5.1 |    73.7 |      85.0 |
| orca-100000      |     266.0 |                         1.1 |   119.2 |     143.9 |
| covertype-200000 |      53.1 |                         9.0 |   103.4 |  1626.7 † |
| orca-200000      |     906.2 |                         2.1 |    75.7 |     139.2 |

† covertype-200000: resumed the original task and retained replay; resume-to-completion was 166.6 seconds. Whole-task and publication-action durations include the failure and repair interval. The prior failed snapshot is retained under failed-publication-attempt/.

## Corrections during the ramp

The first 200,000-row Covertype publication exhausted its coordinator retries after three backend worker restarts. Daytona had completed transformation, clean replay and independent local checks. The publisher loaded all replay outputs plus decoded records and whole-array JSON hashes into memory. It now downloads to temporary disk, streams output records and incrementally hashes reconstructed input. Checksum, row-count, lineage and consumer checks remain intact. Resume permits the transactionally request-keyed publication while still refusing unknown model acknowledgements. The same retained run then published and passed the full external audit; its restarted backend cgroup peaked at 1.66 GiB with no OOM event. The earlier process exit cause was not retained, so memory pressure is a supported diagnosis rather than a proven kernel OOM event.

The 100,000-record Orca run exposed two source answers containing U+2028. The runtime used str.splitlines() to read JSONL, incorrectly treating that character inside a JSON string as a record boundary. The agent recovered without changing text or dropping rows, and the external audit passed. Its retries remain included in the recorded time and cost. The shared reader now iterates physical file lines; regression checks cover U+0085, U+2028 and U+2029 with LF and CRLF records, including retained output round trips.

The first eight runs used runtime c2ba39f456fbe620. The 200,000-record cases use corrected runtime 041df2d9674ed369. The corrected image passed 16 live no-model checks, including Unicode preservation through two replay steps, killed-runner recovery, retained kernel state, clean replay isolation, follow-ups and cancellation. CPU/memory/disk remain 4 CPU / 8 GiB / 10 GiB. This is a capacity ramp with a repaired defect, not an unchanged-release latency comparison.

The accumulated workspace required a 5.37 GB expanded, 1.25 GB compressed checkpoint. Operator limits now allow 8 GiB expanded and 2 GiB compressed, validate paths and check restore disk headroom. Slow Docker transfers and a read timeout required a host-side chunked transfer with per-chunk, prefix-resume and complete-archive SHA-256 verification. The fresh-workspace disk probe was corrected to create its directory before measuring capacity. A verified remote checkpoint is reused on restore retry. Upgrade time is excluded from task timing; the 200,000-record sources were landed during maintenance, so their upload measurements also reflect that activity.

## Interpretation and remaining limits

The agent writes and executes population-level transformations; it does not make a model call per source record. Reported model charges therefore do not grow in proportion to row count. At equal row counts Orca carries roughly nine times the input bytes, plus more expensive text-similarity checks. Rows alone are a poor capacity or latency predictor.

The 200,000-record Orca run deliberately produced training-only chat records and recommended independently sourced, verified maths evaluation questions. It retained all answer variants and made no correctness or holdout-independence claim. Its bounded 4,980-record lexical diagnostic is not comparable to the earlier population-wide grouping checks. The external audit originally assumed partitions; it now verifies every input, target and lineage record independently of partition presence, and records absent partition checks as unknown rather than zero overlap. Nine runs produced verified train/development/final partitions; the tenth passed exact SFT conversion without holdouts.

The largest Orca run took 1,196.5 seconds: preparation 906.2 seconds (including a 2.1-second briefing), model requests 75.7 seconds, publication 139.2 seconds. Everything after preparation took 290.3 seconds. Reported model charges across all ten cases total $1.10045, including the recovered Covertype task. Workspace, storage and maintenance charges are not included.

Source staging currently uploads each file and downloads it again to verify its checksum. This is a remaining transfer bottleneck, separate from briefing generation or model latency; it was observed but not changed in this ramp. Remote checksum verification or a verified compressed staging bundle should be measured next. Persistent source, replay and scratch retention also consumes the fixed 10 GiB disk. These serial trials do not qualify multiple concurrent runs, arbitrary document parsing, unlimited retention, predictive accuracy or the truth of synthetic answers.

Evidence: output/workshop-scale/summary.json, per-case state/task/metrics/audit files, immutable replay/publication receipts and resources.jsonl. No model training was launched.
