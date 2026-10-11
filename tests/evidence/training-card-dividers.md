# Training card dividers

Verified on the local Console, 2026-10-10. Requires the running development
stack and an authenticated local Console session.

## Replay

Open `http://localhost:5173/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8&groupId=0af7dc70-5ab2-48fc-80ed-ce7839836893&jobId=811bbc0e-9c18-418b-9708-fe9f82325e87`.

Inspect the horizontal dividers above the metrics, Run evidence and status/actions.
All three reach the card's inner border while their contents keep 16 px of padding.
Browser measurements at 1280 × 720: card edges x=81 and x=1247; all three divider
edges x=82 and x=1246, meeting the inside of the 1 px card border exactly.

Screenshot: `/private/tmp/training-full-width-dividers.jpg`.

## Checks

From `frontend/`:

```sh
bun run typecheck
bun run lint
```

Both passed. `git diff --check` passed. Test suites were skipped for this
spacing-only change. No data or training operations were performed.
