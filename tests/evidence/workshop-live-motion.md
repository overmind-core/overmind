# Workshop live cell motion — 2026-10-09

## Motion contract

Animate actual cell publication and content/state changes, not initial page load,
unchanged polling, selection, version browsing or dataset metadata. New results
resolve into their measured graph position with a short opacity reveal; updates
briefly wash the existing cell surface. Never translate/scale cells independently
of their connectors, move the camera, imitate typing, or loop indefinitely.
Use existing neutral tokens, no new dependency. Reduced motion uses a shorter,
gentler acknowledgement. Background/offscreen changes do not queue a later show.

## Failure cases and verification

- Initial query, identical refetches and version browsing falsely animate.
- The first source arriving into an empty Workshop is missed.
- A new node animates before layout places it; ports drift during motion.
- Rapid updates, cancellation or unmount replay an old animation or lose content.
- Timestamp/usage changes create noise without a visible content change.
- Reduced-motion and hidden/offscreen states are ignored.
- Animation remounts cell controls, moves focus, or changes natural cell width.

Isolated hook/runtime coverage is justified for update interleavings and reduced
motion that a live MCP publication cannot deterministically schedule. Write that
coverage before implementation, then exercise real local publication through MCP
and the running Console. No paid providers or existing user data modifications.

## Repeatable local verification

Requirements: the existing Compose API/worker and Console on ports 8000/5173,
saved account connection, Bun, uv, and the repository SDK on PYTHONPATH.
No paid providers are invoked. The source fixture contains three synthetic
examples, two eligible and one ineligible. The MCP catalogue was read live:
contract 5.2.0, fingerprint
`f9a5923b7fa4164250eaee0180e6c6752c4770a986977886771c419baef7de83`.

From the repository root:

```sh
PYTHONPATH="$PWD/overmind" UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_motion_mcp.py prepare --project e18b29b5-915d-45a7-80cd-77ffe6559205 --source a3bfd617-2936-4e4c-ac44-602c300d678d
```

Read the returned `get_job(kind=data_exploration)` until completed. Open its
`progress.output_dataset` in the Console and leave space below the source using
the zoom controls. Substitute that dataset UUID in the following command:

```sh
PYTHONPATH="$PWD/overmind" UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_motion_mcp.py publish --project e18b29b5-915d-45a7-80cd-77ffe6559205 --dataset 39376f8c-cfe2-5740-b331-02ba66a85b91 --delay 20
```

Observe without reloading. The harness saves a filter recipe and publishes it
through MCP, with a delay to begin observing. Reruns intentionally create a new
synthetic result. Browser interaction is only for verifying UI, not authoring data.

## Observed results

- Independent fixture: `39376f8c-cfe2-5740-b331-02ba66a85b91`; original source unchanged.
- First publication `55f9c9f0-815f-474c-be0c-e21971210869` completed and appeared
  without reload. Its short animation finished before observation began.
- Final publication `f36471aa-d79c-4833-abab-e26c45f7d5b0` completed. Read-only
  browser sampling captured the entire arrival: opacity 0 while waiting for
  placement, then 0.35 → 1 over approximately 511 ms. At zoom 0.25, its top remained
  630.125 px throughout the fade. All three preceding cells stayed at opacity 1
  and unchanged positions; the camera transform had exactly one distinct value.
  After completion the motion attribute cleared. No cell transform animation.
- A timing review added a placement gate so new nodes cannot reveal at provisional
  coordinates. The final publication above verifies that correction.
- Initial load and responsive resizing had no live motion signals. Desktop
  1280×720 retained 1158 px full-scale cells; mobile 390×844 retained 324 px cells.
  Both screenshots visually retain attached connectors, condition borders and
  existing controls. The browser viewport override was reset afterward.
- Browser error log: empty. The synthetic fixture is retained for reproduction.
- Five focused tests passed before and after the placement adjustment: initial
  load/unchanged polling/usage metadata, first-source arrival, revision-safe rapid
  updates, historical visibility, layout readiness, focus preservation, cancelled
  animations, reduced-motion timing and background suppression. Reduced motion
  and update highlighting were exercised by the component tests, not by changing
  the user's OS preferences or mutating immutable cells through an unsupported API.
- Typecheck, Biome lint, design/contrast/controls and scoped pre-commit checks passed. The full test suite
  was not run for this scoped frontend change. No MCP/backend contract changed.

Focused command from `frontend/`:

```sh
bun run test src/components/datasets/notebook/cell-motion.test.tsx
bun run typecheck
bun run lint
bun run check:all
```

Logs: `/tmp/workshop-live-motion-vitest-final.log`,
`/tmp/workshop-live-motion-mcp-final.log`.
Visual review: `.impeccable/review/workshop-live-motion-desktop.jpg` and
`.impeccable/review/workshop-live-motion-mobile.jpg`.

Impeccable motion/polish guidance kept this to real change acknowledgement with
neutral tokens, reduced motion and no layout redesign; the dataset skill kept
publication on the local MCP. Review was performed inline in the main thread.
