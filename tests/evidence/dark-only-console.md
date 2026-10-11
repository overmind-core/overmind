# Dark-only Console verification

Date: 2026-10-09. Existing local deployment at `http://localhost:5173`;
financial-services project `e18b29b5-915d-45a7-80cd-77ffe6559205`.

## Change

Removed the light palette, theme provider, persisted preference resolution,
operating-system listener, startup theme script and Settings selector. The HTML
declares dark mode before the application loads; CSS uses one root palette.
Workshop and Agent flows and notifications explicitly use dark mode. Existing
dark component treatments are unconditional rather than theme variants.
The two unused theme icons were removed by regenerating the icon table from its
existing vendored paths; no icon artwork was upgraded.

All 108 root token values were compared with the previous effective dark palette:
zero changed values. Inline mention chips and native date-picker chrome retain
their existing contrasting light surfaces; these are not alternate themes.
Reduced-motion behaviour is unchanged.

## Repeatable browser check

1. Before applying the change, use Settings to select Light. Observed the selected
   Light control and light Console. This creates the stale-preference fixture.
1. Apply the change and reload the existing local Console. Observed root class
   `dark`, computed `color-scheme: dark`, and body background `rgb(14, 12, 9)`.
1. Open Settings. Observed Open settings, Integrations and Plan & billing;
   no Light, Dark or System controls. Inspected at desktop and narrow widths.
1. Open a dataset and an Agent capability. Both rendered `react-flow dark`.
   Cells, data, lineage and capability content remained readable.

No dataset mutation, model call, training or evaluation was triggered. Historical
hot-reload sidebar-context errors occurred during file replacement; the reloaded
application rendered successfully without new errors in the inspected logs.
This was browser verification, not an automated cross-browser or OS-theme matrix.

## Commands and observed results

From `frontend/`, using the installed dependencies and Bun:

```sh
bun run typecheck
bun run lint
bun run check:all
bun run build
bun run test src/lib/colors.test.ts src/components/datasets/notebook/flow.test.ts src/components/datasets/notebook/cell-motion.test.tsx
```

Typecheck, lint, design, dark-palette contrast, controls and production build
passed. Targeted tests: 18 passed across three files. The build reported chunks
above its size-warning threshold; tests reported the runtime's unset
`--localstorage-file` warning. Neither failed. Full frontend and backend suites
were not run for this UI-only change; MCP and API contracts are unchanged.

Logs: `/private/tmp/overmind-dark-{typecheck,lint,design,build,tests}.log`.
Final scoped formatting/hooks are recorded in
`/private/tmp/overmind-dark-precommit.log`.
