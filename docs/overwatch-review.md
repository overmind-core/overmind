# Overwatch engineering review

Review date: October 25, 2026.

## Current analytics corrections

The Astra review found three defects after the earlier green CI runs below.
PostHog date bucketing now uses `toDate(toTimeZone(timestamp, 'UTC'))`.
The daily query explicitly allows every day/event group plus an overflow row;
truncated complete aggregates are rejected instead of presented as full totals.
Top-eight tool/command lists may remain intentionally partial.

The private milestone report no longer treats resettable deployment or alias
timestamps as first-time activity. Those two reports explicitly say that the
first-time count and customer list cannot be determined. Other steps report
earliest retained records, without promising lifetime-first history after deletion.
No product models, migrations, emitters or lifecycle writers were changed.

The companion Slack tool description uses the same semantics. Its activation
guide must reuse the installed Overwatch app rather than create another identity.
All enablement and approval gates remain off. The verification snapshots below
are historical; see the current PR checks for the corrected revisions.

## Customer retrieval and PostHog load corrections

Customer milestone reports count matching users separately from the newest-row
query. The database returns at most the requested 25 customers, with deterministic
ordering for ties, while totals still include all matches. Earlier retained
activity across projects and guest exclusions remain unchanged. The old unbounded
milestone-list helper is no longer used or retained as an active path.

Agent PostHog aggregates use the existing Django cache: successful results live
for one hour, unavailable results for 60 seconds. Cache keys separate source host,
project, credential fingerprint, requested days and UTC calendar window. Credentials
are hashed, never included verbatim in cache keys. Product database totals are
still queried on each request. Concurrent cold misses can still perform separate
reads; this cache does not replace the existing admission and rate limits.

Cached aggregates retain their original `window_end`. The companion agent includes
that snapshot time in its final source note and distinguishes it from the report
request time. No customer report is cached, no new database writes or migrations
are added, and all activation gates stay disabled. Current CI and the PR description
are the verification source for these corrections; older results below are historical.

## Scope and read-only boundary

Sam clarified that Overwatch may read Overmind product data. Its notification
state must live outside the product backend. The active PR therefore has no
product database migrations, new models, span index, funnel writer or periodic
notification task. Shared Python and SDK dependencies match main. Core training,
evaluation, inference and billing business logic is unchanged.

Overwatch uses internal read-only analytics endpoints in the existing Django
backend. Slack now runs in the companion Overlord repository using the shared
engine, with no active Slack routes or Celery adapter in the product backend.
Its dedicated service key only grants the bounded analytics query contract;
PostgreSQL enforces a read-only transaction during each request. The existing
Django process still uses its normal database role outside those requests.

Archived delivery prototypes and separate platform security findings are in
[proposals](proposals/README.md). They are not active code or migrations.

## Review fixes retained

- Require an approved Slack user ID before spending query limits or scheduling.
  Verify workspace, app, domain and an unrestricted linked employee again in the
  Slack runtime. Model use is disabled unless explicitly enabled.
- Keep customer records out of model tool results. Replace supplied emails with
  local references and render customer detail locally in private replies.
- Retire broker/model-loop duplication. Avoid question echoes, reject credential-like
  input, store metadata-only audits and fixed errors, deny redirects and prevent
  unfurls or broadcasts. Keep Overwatch out of Overlord history and training traces.
- Bound SQL statements and report time in the synchronous read API, enforce a
  PostgreSQL read-only transaction, and require tool evidence before answering metrics.
- Validate PostHog aggregate shapes and dates. Incomplete or failed responses
  remain unavailable; a genuine empty event set is shown as zero.
- Remove the prototype milestone-event query after its emitter was archived.
  Keep database adoption metrics separate from PostHog conversion definitions.
  Prefer PostHog's native Slack destinations for verified existing events.
- Fix join-date attribution and UTC calendar windows. Distinguish spans from
  traces, membership attribution from actors, and adoption from ordered funnels.
- Replace persistent browser tokens with 30-minute tab sessions, clear legacy
  tokens and cached customer data at logout, prohibit response caching and emit
  restrictive browser security headers.
- Patch the dashboard's Undici dependency and add dashboard lint, typecheck,
  tests, build and vulnerability checks to CI. Avoid persisted checkout tokens.

## Verification and limits

The reviewed code commit is `83ced65`. It passed 3,399 backend tests (15 skipped),
dashboard checks, frontend checks, lint, migration checks and CodeQL in
[CI](https://github.com/overmind-core/overmind/actions/runs/40225646008).
Its 73 focused checks ran against main's backend dependency versions,
including SQL read-only assertions, rejected write methods, denied core project
creation with the dashboard credential, and absent Overwatch models/scheduled
writers. Model state matches migrations. The five dashboard tests, lint,
typecheck, build and isolated desktop/mobile browser checks passed after the
final event-source correction.

The journey suite passed 93 of 94 tests. The six-participant native benchmark
monitor rebuild test failed at `test_native_benchmark_monitor.py:447`, indexing
an empty `arms` list after a pending projection rebuild. The identical test,
participant count, assertion and exception already failed on
[main before this branch](https://github.com/overmind-core/overmind/actions/runs/39353345351).
The monitor service, task, journey and worker-ledger files are unchanged from
that baseline. Earlier review runs also passed all 94 journeys, so a green run
does not establish that the timing issue is fixed.

This is a documented pre-existing merge gate, not a passing check. Core monitor
changes are outside Sam's read-only scope. Pritam must resolve or explicitly
disposition that gate before merge; this review does not authorize a rollout.

Tracked-source secret scanning found no confirmed private credential in the
reviewed Overwatch changes. Production scanner candidates are unchanged lines
from main; new candidates are synthetic test values. This is not an absolute
security guarantee. The dashboard dependency audit is clear; existing backend
and SDK advisories remain separate platform work after their fixes were removed
from this feature PR at Sam's request.

The shared dashboard bearer key is provisional. Browser expiry does not revoke
it server-side. Employee SSO, individual revocation and a production access gate
need review before wider access. Free-text questions may contain sensitive
prose beyond the email/credential filters; model use requires an approved data
policy and the dedicated direct Anthropic project needs an approved retention agreement.
There is no automatic zero-retention routing in this adapter.

Live PostHog compatibility, production identity, query plans on production data
and deployed headers require deployment review. Internal MCP authorization remains a proposal; the shared runtime now has a companion PR. No live customer
queries, Slack messages, PostHog writes or production deployments were performed
by this review. Pritam's review is required before merge and rollout.

## Shared Slack runtime completion

The new integration passes 63 focused backend checks and 19 Node checks, including
complete synthetic slash-command and DM paths. The backend checks assert the
actual PostgreSQL transaction is read-only, preserve dashboard metrics, validate
all five tools, and reject normal customer/project/staff/dashboard credentials.
Node checks cover private terminal reports, argument injection, invalid references,
HR-tool denial, app/workspace/caller restrictions, restricted/unlinked employees,
thread isolation, retry suppression, fail-closed admission and existing Overlord
identity, admin tool, private history and trace behavior. Typecheck, changed-file ESLint and a production Next build pass.
The initial sandboxed build could not fetch its existing Google font; the same
build passed with network access. No package dependencies were changed.

The Overlord work is isolated from the unrelated local Python migration. It is
based on the deployed `origin/main` engine at `3c594a7`. Its setup guide documents
the separate Slack app and credentials, dedicated external admission store,
model-policy approval, disabled-by-default behavior and post-acknowledgement
process-failure limit. These are reviewable activation steps, not a live rollout.
The earlier full-suite/journey results above describe the pre-integration code;
new CI must run on the updated commits. The known main journey gate remains.

Companion Slack runtime PR: [overlord#8](https://github.com/overmind-core/overlord/pull/8).
