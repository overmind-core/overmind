# Run evidence UI removal

Verified on the local Console, 2026-10-10. Requires the development stack and an
authenticated local Console session.

## Replay

Open `http://localhost:5173/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8&groupId=0af7dc70-5ab2-48fc-80ed-ce7839836893&jobId=811bbc0e-9c18-418b-9708-fe9f82325e87`.

The training card proceeds from metrics/progress to its status/actions row with
no Run evidence disclosure. DOM checks found zero Run evidence labels and zero
Download run record buttons. The dataset link, status, Retry action and Run
configuration disclosure remain visible. No actions that alter a run were used.

Screenshot: `/private/tmp/training-run-evidence-removed.jpg`.

## Checks

From `frontend/`:

```sh
bun run typecheck
bun run lint
```

Both passed, as did `git diff --check`. No RunDiagnostics, Run evidence or Download
run record references remain in frontend source. The unused component, its query
subscription and browser download handler were deleted. This only removes the UI;
stored records and backend/MCP contracts are unchanged. Test suites were skipped
for this straightforward UI removal.
