# Overwatch engineering review

Review date: October 25, 2026. Scope: the complete Overwatch branch and the
proposed Overlord/PostHog MCP integration. Review remains active until the
remaining checks and PR handoff are complete.

## Findings fixed

| Area                | Defect                                                                                                                 | Correction                                                                                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Private data        | Customer emails appeared in funnel alerts and customer tool results could reach the model.                             | Alerts use dashboard user references. Supplied emails become local references; customer reports are formatted locally.                                      |
| Authorization       | Slack analytics access relied only on the email domain.                                                                | Require an approved Slack user ID, matching workspace and domain, and an active unrestricted employee account. Model use is explicitly disabled by default. |
| Secret handling     | Task metadata and stored network exception strings could expose questions or reply URLs.                               | Redact Celery argument representations, avoid question echoes, store fixed error codes, reject credential-like input and disable redirects.                 |
| Query correctness   | Synchronous ORM calls ran inside the async model loop.                                                                 | Execute ORM work on the synchronous thread with bounded SQL/report time and connection cleanup.                                                             |
| Grounding           | The model could return an answer before querying data.                                                                 | Require a data tool for analytics answers; missing sources remain unavailable, not zero.                                                                    |
| Delivery            | PostHog was marked delivered when an in-memory queue accepted an event.                                                | Wait for an HTTP ingestion acknowledgement and persist each destination independently. Retry with the same event UUID.                                      |
| Recovery            | Retries depended on rediscovering an event in source tables; PostHog delivery was not bounded per tick.                | Retry durable ledger rows independently. Bound discovery, destination batches and elapsed time.                                                             |
| Serialization       | The dashboard serializer discarded PostHog milestone counts.                                                           | Add the missing fields and test the HTTP/serializer boundary. Validate malformed aggregate responses.                                                       |
| Metric definitions  | Spans were labeled traces; membership activity implied individual action; adoption was described as conversion.        | Correct labels, expose attribution and retention limits, and leave ordered conversions to PostHog.                                                          |
| Time windows        | Rolling backend windows did not match calendar-day charts.                                                             | Use UTC calendar days and anchor charts to the cached report timestamp.                                                                                     |
| Browser data        | The shared token survived indefinitely in localStorage.                                                                | Use a 30-minute tab session, clear legacy tokens and query memory at sign-out/expiry, prohibit caching, and emit a restrictive CSP.                         |
| Configuration       | The public example contained live workspace/channel identifiers.                                                       | Supply those identifiers privately through deployment settings.                                                                                             |
| Dependency security | The locked toolchain and backend included published networking, request parsing, tracing and image parsing advisories. | Patch Undici, DRF, OpenTelemetry and Pillow. The compatible Docling packages also advance for Pillow 13; document-parser CI is required before merge.       |
| Continuous checks   | The separate dashboard had no CI typecheck or build job.                                                               | Add its lint, typecheck, session tests and production build to CI.                                                                                          |

## Evidence

- All 64 focused backend security, delivery, analytics and Celery topology
  regressions pass against an isolated PostgreSQL instance. Outbound calls use network
  fakes; no live Slack messages, customer queries or PostHog writes were sent.
- All five dashboard session, request and date tests, typecheck, lint and the
  production build pass. The login, desktop overview, users table and mobile
  activation view passed isolated Chrome checks with synthetic API responses,
  no page errors and no horizontal overflow. The visual check caught and fixed
  a UTC chart-label date shift and narrow mobile header/step layouts.
- Model state matches migrations; the branch is based on the current main
  migration graph. Required pre-commit checks run over the entire branch, including private-key
  detection and the test-boundary check.
- The dashboard dependency audit and a scan of all 235 locked backend packages
  report no known vulnerabilities after the security updates.
- Repository secret scanning found candidates in test fixtures, local
  development defaults and the existing public PostHog ingestion token. No
  confirmed private credential was found in the reviewed Overwatch changes.
  New scanner candidates are test fixture values; production candidates are
  unchanged lines from main. Scanner output contains fingerprints and locations,
  never credential values.

## Remaining boundaries

The shared dashboard admin token is still a provisional internal access method.
Browser expiry is not server-side key expiry: the shared bearer credential
remains valid until rotated. Employee SSO, individual revocation and a production
access gate need deployment review. Session storage and CSP reduce exposure; they do not provide SSO.

The MCP design is documented, not implemented. Internal service authorization
and employee capability checks must precede tool discovery or invocation. Normal
customer/project MCP credentials must never reach company-wide analytics.
PostHog MCP must use a project-scoped read-only connection and a local tool
allowlist. The two Slack profiles should share governed runtime infrastructure.

Free-text employee questions can still contain sensitive prose that simple
credential/email filters cannot recognize. Model use is opt-in and requires an
approved provider data policy. Named customer records are kept out of model tool
responses. Do not paste trace contents, secrets or sensitive documents into chat.

Live PostHog query compatibility, deployed Cloudflare headers and production
identity configuration require deployment checks. Offline tests do not certify
those external systems.

The first PR CI run passed the full backend suite, dashboard, frontend,
migrations, lint and CodeQL. One of 94 journeys failed while rebuilding the
existing native benchmark monitor after its projection files were removed.
That code and its journey are unchanged by this branch; the failure remains an
open verification issue rather than an assumed unrelated failure. The security
dependency updates require a fresh CI run, including document-parser coverage.
Pritam's review remains required before merge and rollout.
