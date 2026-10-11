# Million-row Workshop pagination

Local verification on 2026-10-10 with the running Docker Compose API on
`http://localhost:8000` and Console on `http://localhost:5173`. Project discovery
through local MCP confirmed the endpoint and project before testing.

Dataset `ecabb7ed-4b75-4f2b-8572-35223f1c414c`, cell
`e5563a89-f9b2-4dfd-bd68-c1dc6c71babd`: 1,034,657 rows, 15 visible columns,
910,197,806-byte Parquet file. Its recorded parent belongs to dataset
`0b0d60b5-d321-416d-8881-f5934558d337`; it is not stored in the output dataset's directory.

## Cause and change

Ordinary pagination numbered and sorted the full table through DuckDB before
applying LIMIT/OFFSET. Direct profiling measured 1.76–1.79 seconds for page reads
and 0.04–0.05 seconds for their change highlights. Deep offsets exhausted DuckDB's
512 MB memory allowance. REST requests at offsets 10,000 and 1,000,000 returned 500.

Unfiltered pages in physical row order now obtain the total from Parquet metadata
and use the existing positional reader to decode only the relevant row groups.
Explicit ascending/descending `_index` order follows the same bounded read path.
No data files, lineage, page response contracts, MCP tools, or frontend behavior
were changed. Sorting by a data column, filtering and search retain their query path;
the latency measurements below do not claim those operations are accelerated.

Regression risks checked: crossing storage-group boundaries, partial/empty final
pages, reverse order, original row positions, JSON/nullable/precision-sensitive
values, source identities, and existing sort/filter/diff behavior. The new API
case was written and passed before the implementation changed; live timings
captured the actual performance failure before the fix.

## Repeatable live API check

Requires the same local dataset and an address-bound account connection in
`~/.config/overmind/connection.toml`. Credentials are read locally and never logged.
From the repository root:

```sh
PYTHONPATH=overmind .venv/bin/python tests/evidence/workshop_pagination_replay.py /private/tmp/pagination-replay.json --compare tests/evidence/workshop-pagination-before.json
```

The replay performs five serial GET requests to the Console's rows endpoint with
`diff=1`, checks totals and exact row indices, and hashes the complete JSON response.
All previously successful responses retained identical hashes, including diff marks,
metadata and full values. All five requests succeeded after the fix.

|    Offset | Rows |            Before | After, warm | Result               |
| --------: | ---: | ----------------: | ----------: | -------------------- |
|        10 |   10 |           2.083 s |     0.101 s | Same response hash   |
|        20 |   10 |           1.837 s |     0.079 s | Same response hash   |
|         0 |   50 |           1.825 s |     0.081 s | Same response hash   |
|    10,000 |   25 | 500 after 6.016 s |     0.079 s | 200, correct indices |
| 1,000,000 |   10 | 500 after 6.484 s |     0.104 s | 200, correct indices |

Records: `workshop-pagination-before.json`, `workshop-pagination-after.json`,
`workshop-pagination-warm.json`. The first request immediately after the edit
took 3.587 seconds; that observation is retained separately rather than omitted.
Repeating after hot reload settled produced the warm timings above. These are
local observations, not production latency guarantees or a concurrency benchmark.

## Browser journey

Using the Codex in-app browser at 1280 × 720, open
`/datasets/ecabb7ed-4b75-4f2b-8572-35223f1c414c?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8`
with an authenticated local Console session. Reset view, then:

1. Click the active cell's next-page button at 10 rows per page. Wait for row 10:
   264 ms; 10 rows displayed, footer reads 11–20.
1. Select 50 in Rows per page. Wait for row 49: 274 ms; 50 rows displayed, footer
   resets to 1–50 on page 1.
1. Pan down on the empty canvas beside the cell to reach the footer. Click next:
   301 ms; 50 rows displayed, footer reads 51–100 on page 2.

Timings include browser automation overhead, measured from click initiation until
the expected row exists. Screenshot: `/private/tmp/workshop-pagination-50-rows.jpg`.
Measurements: `/private/tmp/workshop-pagination-browser.json`.

## Regression checks

```sh
.venv/bin/pytest tests/test_dataset_api.py tests/test_dataset_store.py tests/test_dataset_streaming.py tests/test_dataset_scale.py tests/test_dataset_chain.py -q
.venv/bin/ruff check overbae/services/datasets/store.py tests/test_dataset_api.py tests/evidence/workshop_pagination_replay.py
```

43 tests passed; Ruff passed. Test log: `/private/tmp/workshop-pagination-tests.log`.
The test database requires local Postgres. Test warnings concern the existing
short test JWT key and synchronous consumption of an export stream.
No training, evaluation, deployment, dataset mutation, commit, or push occurred.
