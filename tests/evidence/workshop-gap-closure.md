# Workshop gap closure

This continuation addresses platform gaps, not agent-authored transformation meaning.
MCP-ready: measured execution/landing phases, bounded queries and extraction evidence.
CLI-guided: original-byte downloads; atomic multi-file ingestion remains a gap.
No browser, existing recipe rewrites or original dataset mutation. The user's
subsequent authorization extended this pass through real training and comparison,
with a $100 maximum and deliberately small bounded jobs.

Failure modes recorded before implementation:

- Short scripts pay a two-second polling floor per step and queued scripts wait
  another five seconds; improvements must preserve isolation, timeouts, cancellation
  and exact output/lineage. Preview must expose measured aggregate duration.
- A slow query can consume resources indefinitely or block unrelated reads;
  timeouts must return a structured error, release the connection, and leave the
  source and later queries intact. Output limits must apply during materialization.
- Structured landing has no measurable storage/profiling phases. Progress must
  not claim a file is published before atomic publication or lose cancellation.
- Native text can be partially missed on a page with otherwise valid extracted
  text. Recovery must preserve coordinates and duplicate observations, avoid
  replacing semantic content and disclose ambiguous coverage.
- Agents cannot inspect source bytes through the CLI, preventing independent
  review of reading order, tables and diagrams. Downloads must verify the source
  checksum, enforce project access and never overwrite without permission.
- Serial file upload exposes intermediate versions and cannot atomically land a
  batch. A batch must bind ordered file identities and its destination; retries,
  interrupted bytes, wrong hashes and changed recipes must not duplicate or
  partially publish. A bad member must preserve the preceding readable version.
- An unexplained inspection latency outlier requires repeat and concurrent-load
  evidence; a later fast response does not establish a root cause.

## Observed outcome

Fresh live matrix: 38/38 expected outcomes, including malformed/oversized input
rejections. Repeated real-data transformations: 9 document/tabular/chat runs plus
6 complex-handbook runs. These used real local MCP and CLI transfers, not browser
automation or direct ORM writes. See `workshop-user-wide-results.json`,
`workshop-user-platform-results.json`, `workshop-user-documents-results.json`, and
`workshop-user-training-journey.md` in this directory.

Implemented and retested: bounded query execution/materialization; lower isolated
execution polling latency and measured phases; accurate stopped-runtime receipts;
actionable package parameter errors; original-source checksum-verified exports;
native-only readiness; rejection of ignored batch overrides; correct requested
baseline in estimates; renewable training collector leases; terminal operational
receipt recovery; worker-receipt-based training billing and duration evidence.

Still open: atomic/resumable multi-file batches; partial native-text omissions on
otherwise-extracted PDF pages; granular structured landing storage/profiling stages;
all-in provider accounting. The earlier isolated slow inspection was not reproduced
under fresh concurrent reads; its original cause remains unknown. These gaps are
not labelled fixed merely because supported-path tests passed.
