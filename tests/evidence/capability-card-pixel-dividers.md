# Capability card typography and dividers

Verified on the local Console, 2026-10-10. Requires the development stack and
an authenticated local Console session.

## Replay

Open `http://localhost:5173/?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8`.
Inspect the KYC, Parcel Grounding, SOC Analytics and Fixture Fact Lookup cards.

All four card names use the computed font family `"Geist Pixel", system-ui, -apple-system, sans-serif`. All metric-row dividers meet the inside of their
card borders; row content retains 20 px of horizontal padding. At 1280 × 720,
the first card spans x=81–459 and its metric rows span x=82–458. The other three
cards have the same 1 px border alignment.

Screenshot: `/private/tmp/capability-card-pixel-dividers.jpg`.

## Checks

From `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
```

Typecheck, lint, design, contrast and controls passed. `git diff --check` passed.
Test suites were skipped for this typography and spacing change. No platform
data was modified.
