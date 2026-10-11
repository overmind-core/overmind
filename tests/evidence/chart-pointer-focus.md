# Chart pointer focus

Date: 2026-10-10

The focusable Recharts SVG received the browser's automatic 5 px outline after a
mouse click even though `:focus-visible` was false. A shared CSS rule suppresses
that outline only for pointer focus. Chart keyboard behavior remains enabled.

## Browser replay

Use the existing local Docker stack and open:

```text
http://localhost:5173/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8&groupId=9e61cda4-9bdc-4655-af5d-01aea112a876&jobId=535888b9-70ac-43c1-96c0-164fc9f54762
```

1. Scroll to Metrics and click inside Validation loss.
1. Inspect the active element: `svg.recharts-surface`, `:focus-visible` false,
   computed outline style `none`. Before the change its outline style was `auto`.
1. Press Shift+Tab to focus the Validation loss help button, then Tab back to the
   graph. `:focus-visible` is true and the automatic focus outline is visible.
1. Press ArrowRight. The tooltip displays Step 40, Qwen3 0.6B, 0.6666.

Observed: clicking no longer outlines the plot; keyboard focus and arrow-driven
tooltip navigation still work. Screenshot after pointer click:
`/private/tmp/chart-pointer-focus-fixed.jpg`.

## Static checks

Run from `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
```

All passed. No unit suite was run for this CSS-only behavior change; the browser
replay above exercises actual pointer and keyboard focus.
