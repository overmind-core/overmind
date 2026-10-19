# Staging OCR benchmark

Candidate: `35164343` (OCR with rolling-deployment compatibility, transfer language snapshots and unsupported-script provenance).

Baseline: `51b7c2313719099fe48cc483f0ad7b89addb3350`. Fixed PDF samples retain the same bytes/checksums across variants. Runs are serial in one staging project. Candidate extraction reported a one-worker limit in staging; these timings therefore do not establish a staging speedup from parallel page execution. The underlying staging CPU/memory allocation was not independently measured. Each sample/variant has one observation; these are paired smoke measurements, not a production load or accuracy study.

| Sample | Baseline landing | Candidate English | Automatic language | Cached automatic |
| ------ | ---------------: | ----------------: | -----------------: | ---------------: |
| 24-a   |          17.137s |           12.865s |            16.107s |           4.592s |
| 24-b   |          19.390s |           13.439s |            21.395s |           4.751s |
| 24-c   |          28.086s |           16.461s |            30.940s |           4.699s |
| 96     |          65.216s |           36.249s |            68.409s |           5.109s |

The measurements support improved OCR performance: comparable English extraction
took 25–44% less time across all four samples, reuse avoided every OCR page, and
intermediate progress publications fell while completion was retained. The local
worker comparison below separately demonstrates faster parallel extraction.

For the 96-page sample, explicit English landing took 44.4% less time than the
baseline, and cached automatic extraction took 92.5% less time than cold automatic
extraction. The default automatic cold path took 4.9% more time than the old
English-only baseline; automatic extraction also performs language-selection
work. These observations do not isolate individual changes or control staging host
allocation across deployments; they do not establish a speedup for every default
cold import.

## Output, cache and backend events

| Variant        | Sample | Rows | OCR worker limit |  Reused pages | OCR progress events |
| -------------- | ------ | ---: | ---------------: | ------------: | ------------------: |
| baseline       | 24-a   |   52 |     not recorded | not supported |                  25 |
| baseline       | 24-b   |  120 |     not recorded | not supported |                  25 |
| baseline       | 24-c   |  242 |     not recorded | not supported |                  25 |
| baseline       | 96     |  570 |     not recorded | not supported |                  97 |
| candidate-eng  | 24-a   |   52 |                1 |             0 |                   5 |
| candidate-eng  | 24-b   |  120 |                1 |             0 |                   6 |
| candidate-eng  | 24-c   |  242 |                1 |             0 |                   7 |
| candidate-eng  | 96     |  570 |                1 |             0 |                  16 |
| candidate-auto | 24-a   |   52 |                1 |             0 |                   6 |
| candidate-auto | 24-b   |  120 |                1 |             0 |                   8 |
| candidate-auto | 24-c   |  242 |                1 |             0 |                  11 |
| candidate-auto | 96     |  570 |                1 |             0 |                  28 |
| reused-auto    | 24-a   |   52 |                1 |            24 |                   1 |
| reused-auto    | 24-b   |  120 |                1 |            24 |                   1 |
| reused-auto    | 24-c   |  242 |                1 |            24 |                   1 |
| reused-auto    | 96     |  570 |                1 |            96 |                   1 |

## Checks

All four samples passed the same checks:

- Baseline and candidate English: identical row counts, page coverage and ordered text hashes.
- Cold and cached automatic extraction: every ordered row value matches, including regions and provenance, when compared in a fixed column order.
- Cached automatic extraction: every page reused, with no pages newly OCR'd.

Cold and cached imports expose different column order (`text, page` versus `page, text`), so their whole-file fingerprints differ. All six row fields, including source identity, page regions and recognition provenance, match in source-row order when compared with a fixed column order. Cache reuse preserves the extracted values; this benchmark does not claim identical file identity or column presentation. Complete backend event replays retain contiguous sequence numbers and terminal events, with no internal checkpoint payloads exposed.

## Local parallelism check

The same 96-page PDF was also extracted in a network-isolated local container limited to two CPUs and 2 GiB. This separates worker count from language selection; it is not a staging throughput claim.

| Mode      | One worker | Two workers |
| --------- | ---------: | ----------: |
| english   |    28.351s |     17.165s |
| automatic |    55.497s |     34.880s |
| cached    |     0.134s |      0.120s |

## Retained source identities

| Sample | SHA-256                                                            |
| ------ | ------------------------------------------------------------------ |
| 24-a   | `c03e4b1f3c36ef62f4a336f215034383aa5fb79db80ac1bdc6e76fd04032cc07` |
| 24-b   | `416604ab0fe65239c8dc81870f0bfa2228cac46bd7ee9dc33feeec4f0d74042a` |
| 24-c   | `fc2384c9c5f4ae64000a1e6747999cfa182144885eb948dd92b5db1d72a2e6cc` |
| 96     | `c7cc5d7cc5413146ab610c68c67ee71dc437b6ebd62588d8cc7fe0819832d17a` |

## Dataset receipts

Staging project: `16563f1e-d63b-4225-a72d-19a2ebcc4577`. Dataset IDs are readable through staging MCP `inspect_dataset` and `get_job(kind=dataset_run)`; the account connection must remain bound to the staging endpoint.

| Variant        | Sample | Dataset                                |
| -------------- | ------ | -------------------------------------- |
| baseline       | 24-a   | `73c98dcd-f9ac-4ac8-9a79-0e98234829a7` |
| baseline       | 24-b   | `35e1664d-7a31-4fb5-afa3-cbfeb25c82c3` |
| baseline       | 24-c   | `0287302b-4c0b-4b7d-a9a8-454ea087b048` |
| baseline       | 96     | `9d090e5f-ea73-4cb6-86cf-9ee0133cf818` |
| candidate-auto | 24-a   | `2c43fff7-3edb-4632-bd59-0b110939f5ae` |
| candidate-auto | 24-b   | `ee93c5db-263e-4b81-b184-a6f34c20de4a` |
| candidate-auto | 24-c   | `a12d4724-6800-419a-9bca-f9f040352f03` |
| candidate-auto | 96     | `a62e074a-1c4f-4d9c-8c19-cbc8393a7f0b` |
| candidate-eng  | 24-a   | `4ff395ce-d812-42ea-bc2b-dad8cc7f29e0` |
| candidate-eng  | 24-b   | `b2d435db-f9c1-413b-a0fa-80442c659a48` |
| candidate-eng  | 24-c   | `31e514ee-82d3-428a-941d-adfe0b462a5f` |
| candidate-eng  | 96     | `dbb4b2a2-7ba6-4c27-9aac-5e21e6afdff4` |
| reused-auto    | 24-a   | `99c39caa-dfbb-498c-955e-7f9d32cab509` |
| reused-auto    | 24-b   | `40df17b6-443f-48d5-b81a-5d6644af4a19` |
| reused-auto    | 24-c   | `84d2c15e-12e7-4388-a39d-5612df033f78` |
| reused-auto    | 96     | `0d6df44e-a9fe-4bce-8b98-63f267b3397f` |

## Regressions found during staging

The initial candidate lost MCP language preferences on the CLI transfer path. The corrected transfer snapshots preferences into both initial-import and append receipts, and repeated completion preserves them. Four policy/retry cases and the strengthened MCP/CLI/OCR journey passed.

Automatic script detection also labelled two Spanish screenshots as Fraktur and rejected a whole PDF. Policy version 3 retains that unsupported guess, uses English for the first recognition pass and allows stronger text evidence to select a supported language. The exact failing screenshot is retained as a regression fixture. The final document/transfer regression run passed all 68 tests; a local two-CPU/2 GiB check completed all 96 pages in explicit English, automatic and cached modes with exact cached-row equality. Earlier policy-version-2 timings are diagnostic only and excluded from the tables above.

Staging rollout: [GitHub Actions run 39213207362](https://github.com/overmind-core/overmind/actions/runs/39213207362). The full application image and every service passed the ordered deployment health gates.

## Measurement scope

Landing seconds come from durable admission start/finish timestamps and exclude queue delay and client polling. OCR-only seconds, client-observed time and queue delay remain in each result receipt. Event counts come from complete backend SSE replays; they measure publications, not a direct PostgreSQL query count. Internal checkpoint payloads must not appear in those events. Language changes may legitimately change automatic-mode OCR text; they do not establish better accuracy without labelled evidence. The benchmark does not exercise concurrent tenants, forced worker crashes or large-file memory limits. Recovery and project isolation have local regression coverage; large-file memory stress was not performed.

No production deployment, training, evaluation or inference is part of this benchmark.
