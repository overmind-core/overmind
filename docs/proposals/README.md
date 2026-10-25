# Archived Overwatch write-path prototypes

These `.example` files are design references, not executable application code,
active Django migrations, registered models or runnable tests.

Sam clarified that Overwatch must consume the Overmind backend with read-only
access. Notification history, deduplication and retries therefore belong in
Overwatch's own storage. PostHog should supply conversion events and aggregates.
The active product backend has no Overwatch delivery table, periodic delivery
task, new index or event emitter.

Do not apply the archived migrations to the Overmind product database. Port any
useful delivery logic to the future Overwatch service and replace the foreign
key with a stable external customer reference. PostHog notifications and Slack
channel routing need a separate implementation review.

The proposed span index is a separate core-platform optimization for Pritam to
assess. Shared Python dependency updates were also taken out of the Overwatch
PR. Their reviewed patch is preserved in commit `3853b02` and the local branch
`codex/overwatch-shared-infrastructure-review`; it includes DRF, OpenTelemetry,
Pillow and compatible Docling updates. Existing backend/SDK security advisories
remain a separate platform follow-up. The dashboard's own Undici fix stays in
this PR.
