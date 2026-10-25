# Overwatch engineering review

Review date: October 25, 2026.

## Scope and read-only boundary

Sam clarified that Overwatch may read Overmind product data. Its notification
state must live outside the product backend. The active PR therefore has no
product database migrations, new models, span index, funnel writer or periodic
notification task. Shared Python and SDK dependencies match main. Core training,
evaluation, inference and billing business logic is unchanged.

Overwatch still uses internal read-only analytics endpoints in the existing
Django backend. Its Slack command/event ingress accepts questions; it does not
mutate product records. These are application-level read-only interfaces, not a
claim that the existing Django process uses a database role limited to SELECT.
A future Overwatch service credential must be restricted to analytics reads.

Archived delivery prototypes and separate platform security findings are in
[proposals](proposals/README.md). They are not active code or migrations.

## Review fixes retained

- Require an approved Slack user ID before spending query limits or queueing.
  Verify workspace, domain and active unrestricted employee status again in the
  worker. Model use is disabled unless explicitly enabled.
- Keep customer records out of model tool results. Replace supplied emails with
  local references and render customer detail locally in private replies.
- Redact task argument metadata, avoid question echoes, reject credential-like
  input, store fixed errors, deny redirects and prevent unfurls or broadcasts.
- Run ORM work on the synchronous thread. Bound SQL statements and report time,
  clean up connections, and require tool evidence before answering metrics.
- Validate PostHog aggregate shapes and dates. Incomplete or failed responses
  remain unavailable; a genuine empty event set is shown as zero.
- Fix join-date attribution and UTC calendar windows. Distinguish spans from
  traces, membership attribution from actors, and adoption from ordered funnels.
- Replace persistent browser tokens with 30-minute tab sessions, clear legacy
  tokens and cached customer data at logout, prohibit response caching and emit
  restrictive browser security headers.
- Patch the dashboard's Undici dependency and add dashboard lint, typecheck,
  tests, build and vulnerability checks to CI. Avoid persisted checkout tokens.

## Verification and limits

Earlier review iterations passed 3,390 backend tests and all 94 journeys. One
preceding run had a transient native benchmark monitor failure that did not
recur; its underlying timing issue is not claimed fixed. The final read-only
scope passed 73 focused checks against main's backend dependency versions,
including SQL read-only assertions, rejected write methods, denied core project
creation with the dashboard credential, and absent Overwatch models/scheduled
writers. Model state matches migrations. Final full CI is pending. The five dashboard tests,
lint, typecheck, build and isolated desktop/mobile browser checks passed.

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
policy and requests zero retention routing.

Live PostHog compatibility, production identity, query plans on production data
and deployed headers require deployment review. Internal MCP authorization and
the shared Overlord runtime integration remain proposals. No live customer
queries, Slack messages, PostHog writes or production deployments were performed
by this review. Pritam's review is required before merge and rollout.
