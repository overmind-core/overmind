---
name: frontend-design
description: Overmind Console design system — semantic tokens, shared primitives, geometry and icons, the border-contrast floor, the duplicated table implementations, and the verification scripts. Use when writing or changing frontend UI, styles, tables, or component markup.
---

# Frontend design conventions

The Console is "a quiet workshop for agent improvement" — warm minimalism, copper/gold brand, Geist Pixel for body, card, section and popup text, PP Mondwest only for main page titles, and NeueBit for navigation, small radii, flat surfaces. Keep it; don't drift toward default Tailwind.

The token spec is the root `DESIGN.md` — extend its values, don't restructure it. The Console is dark-only: palette values live on `:root` in `frontend/src/styles.css`, exposed under `@theme inline`. First paint, authentication, toasts and flow canvases stay dark regardless of stored or OS preferences. Do not add a theme selector, theme provider or preference listener.

## Color — semantic tokens only

- **Never** raw Tailwind palette utilities (`bg-green-500`, `text-blue-600`, `border-amber-400`) or hex in class strings (`bg-[#1c1917]`).
- **Status** → the four semantic tokens as a tint triplet. The palette is fixed, so **no `dark:` variants**: `border-success/40 bg-success/10 text-success`. Meanings: `success` = passed/healthy/connected/completed; `warning` = review/degraded/stale; `info` = running/pending/queued/training; `destructive` = failed/error/cancelled. Prefer the components — `<Badge variant="success|warning|info|error|neutral">` or `<StatusBadge status={domainStatus(state)}>` (`lib/job-status.tsx`, `lib/colors.ts`).
- **Categorical** — a fixed set of entity types, not status → `text-cat-1 … text-cat-6` / `bg-cat-1/10`. Assign a stable slot per type.
- **Login / onboarding**, the always-dark cinematic surfaces → the named `--auth-*` tokens (`bg-auth-panel`, `border-auth-border`, `text-auth-text-dim`, `text-auth-accent`, …), never inline hex.
- Neutrals → `bg-background/card/muted`, `text-foreground/muted-foreground`, `border-border`. Titles use `text-foreground`, never `text-black dark:text-white`.

## Border contrast — hard constraint

`--border` at full strength is only ~1.3:1 against a card; the visibility floor in `scripts/check-contrast.mjs` is 1.2. The sanctioned ramp:

- outline = bare `border-border`
- divider = `border-border/70`
- faint = `border-border/60`

Anything below `/60` is invisible. Never lower a border opacity without running `bun run check:contrast -- --all` and reading the border rows.

## Shared primitives — use them, don't hand-roll

- A page title is `<PageHeader title description count actions icon leading/>`. Never a raw `<h1>`.
- A page scaffold is `<PageShell variant="scroll|full" header/>`.
- Loading is `<Spinner size/>` inline (buttons) or `<LoadingState label fullPage/>` for a region; `Skeleton` when the layout is known on first paint. Never a raw `animate-spin` or a "Loading…" text block.
- An empty state is `<EmptyState icon title description action size/>`.
- A titled card is `<SectionCard title …>`; plain `<Card>` otherwise. Don't hand-roll `rounded-lg border` surfaces.
- A destructive confirm is `<ConfirmDialog destructive onConfirm/>`. Never `window.confirm`. Dialog Cancel is always `variant="secondary"` (what `AlertDialogCancel` renders).
- Feedback: `sonner` toast = transient async result; `Alert` = persistent inline notice; `DismissibleAlert` = in-dialog mutation error.
- Model choices use `ModelOptionLabel`: provider logo, model name, then separate metadata. Use it in both dropdown rows and selected values, including judge, benchmark, comparison and filter selectors. Keep fit/cost/size annotations and selection rules; model IDs remain the stored values. `ModelProviderChip` is for read-only badges, not dropdown rows.

## Geometry, type, icons

- **Flat surfaces** — standard `shadow-*` utilities remain disabled. Depth = surface layering + 1px borders. Shared confirmation dialogs alone use `shadow-confirmation`, a subtle palette-matched drop shadow requested by the user.
- Radii: `rounded-sm` for interactive primitives, `rounded-md` for containers. `rounded-lg/xl/2xl/3xl` are off-system. The Workshop bezel uses the same `rounded-md` (3px) as other pages. Dataset navigation shares the outer bezel with the content; its 248px grid column starts collapsed and expands with the notebook's folder toggle, with a reduced-motion alternative. Rows are 28px on desktop and 36px on touch layouts, with no gaps between items. Mobile reveals the same navigation in the bezel while retaining the hidden notebook; it does not create a separate pane.
- Main page titles use `TITLE.page` (PP Mondwest). Card, section, dialog, sheet and empty-state headings use their `TITLE` role in Geist Pixel. Never add Mondwest outside the main page title. `font-mono` also resolves to Geist Pixel; Fira Code is not shipped.
- Icons: **always the central registry** — `import { Icon } from "@/components/ui/icons"` → `<Icon.name className="size-4" />`. The glyphs are pixelarticons paths vendored into `ui/icons/glyphs.ts` (24×24 grid, 2px inset, `currentColor`); never import that module or `lucide-react` in app code; both fail `check:design`. One concept = one glyph (`Icon.delete` = trash, `Icon.close`/`Icon.failed` = the "X"). Missing a glyph? Add `name: glyph("<svg-name>")` to `ui/icons/index.ts`, the only file that may read the table, then `bun run icons:vendor` with `PIXELARTICONS_LICENSE_KEY` set (or `--source <svg dir>` of an unlocked package) — typecheck fails until the table is regenerated. Removing one is the reverse. There is no icon package to install. Sizes: `size-4` in buttons, `size-[17px]` in nav, `size-3` for dense chips.

## Two duplicated table implementations

Workshop cells retain their existing markup and controls inside the same React Flow
infrastructure used by Agent. Cell headers drag on a 20px grid; elbow edges follow
recorded lineage. Steps cascade downward, with sibling branches side by side on
the same horizontal layer. Cells open full-width at scale 1. Preserve max-w-7xl
and original notebook padding; explicit zoom and Fit view change only the camera.
Reset and linked-cell focus restore scale 1; resizing preserves the chosen zoom.
Reuse Agent trajectory controls and its toggleable, persisted minimap treatment.
The minimap toggle shares the right-hand control box with the dataset sidebar
button; there is no cell-search button or cell-selector strip.
Positions persist per project/dataset in the browser and never
change execution. Canvas wrapper styles must not redesign cells or the rows grid. Corrections replace the
displayed process; there is no repair-history or restore view. Verified source,
script, derivation and partition dependencies connect related datasets. Condition chips sit just above destination handles,
with an opaque card surface and input-token border, not on the shared branch rail.

A table fix applied to one codepath silently misses the other:

1. `src/components/ui/data-table.tsx` — the shared TanStack `<DataTable>` (Traces, Sessions, Runs, Datasets, Experiments, Billing)
1. `src/components/datasets/notebook/rows-grid.tsx` — the paged rows grid every dataset table (source, cell output, product) renders through

Sort headers are in `components/ui/sortable-header.tsx` for hand-built tables and inline inside `data-table.tsx` for the shared one — check both before declaring a header fix done.

Eval results align model variants by the generated client's `rowIndex`, including zero. Trace-filter samples use `sourceTraceId`; samples with neither keep their own identity. Use `components/evaluations/sample-index.ts` for table and detail lookups so unrelated outputs never collapse or stand in for another model's missing response.

The generative eval datapoint popup selects one model at a time and shows its input beside its generated output, grading reference, and evaluator reasoning. `SampleInput` consumes the generated `EvalSampleIO` contract: captured initial runner requests include system prompts and tools; historical dataset inputs explicitly say the exact request was not captured. Never label a combined trajectory as input or an extracted statistical prediction as the model's full output.

The native probability datapoint popup shows shared frozen input on the left and all models' outputs in a vertically scrolling list on the right, without model tabs or a selector. Each model keeps its own probabilities, references, scores and missing/error state. Narrow screens stack the input above the model list.

Trajectory cards lead with the behaviour name and a compact source-text preview. Use compact raised-wash headers with the existing console grain asset, categorical icon tiles and inset role labels for task, model call, execution step, tool, local outcome and result. Group input/output and continuation facts in an unbordered subtle-wash inset, with labels above values for a full-width reading measure. Use spacing to separate card content, retaining only the evaluation-footer rule. Model headers combine provider identity and a full-width model chip; the behaviour sentence appears once in the body. Trajectory nodes have no detail sidebar or clickable header. Long on-card text stays available in bounded, keyboard-focusable scroll regions; model calls retain `ModelProviderChip`, including an explicit missing-model chip. The instructions preview opens the full system prompt directly in a dialog. Mode-specific model/prompt context requires an exact step-anchor match; unbound or shared calls show only context common to their candidates. Never borrow a conflicting worker prompt. Excerpts stay labelled as excerpts; missing capture has no expand control.

Trajectory layout keeps the source-authored execution steps, conditional routes and terminals on the main graph. Merge steps only when their labels, source anchors, kind and execution contracts agree. Connect available actions to their owning decision through compact category nodes on the same graph, using the central tool icon and a header chevron. Expanded tools and outcomes are independent canvas nodes, never nested cards or parent containers. Neutral icon controls expand paths without selecting a node. Tool outcomes expand from the Outcomes row in the card body, with the count and chevron forming one control; never put this control in the tool header. Explicit source-backed transitions connect outcomes to result processing, retries and the next decision; keep a summary return connection when a group is collapsed. Never infer a destination from continuation prose. Continuation edges do not expand structural search ancestry. Expanding an action reveals source-cited conditions, local outcomes and continuations in place; preserve viewport context and relayout measured heights. Dashed action edges express available choices, not observed execution. Never create a separate execution overview or action-only navigator. Branches collapse in place; keep shared nodes reachable through open routes. Search the complete graph, reveal hidden matches with their incoming paths, and restore the collapsed state when search clears. Put search in a popover opened from the canvas actions, alongside expand-all and collapse-all; do not add a separate top toolbar or legend. Group canvas actions into a neutral bordered dock and use a matching zoom/fit dock. Open with the whole visible flow fitted to the canvas, including connector routes and condition labels outside node bounds. Fit view restores that overview; search frames the matching path. Use the outward/inward arrow glyphs for global expansion/collapse, and the shared tooltip on every canvas action, including search; native title attributes are not a substitute. Reserve layout space for static condition labels on connectors; do not add duplicate dropdowns for the same rule. Sibling outcomes share a row beneath their tool, with the first outcome aligned for a straight incoming connection. Remaining branches fan out above their conditions without reversing direction. Return paths join below the tallest sibling before the next tool starts. Category widths grow with the complete outcome row; return rails stay beyond the rightmost visible descendant. Keep outcome source handles mounted so disclosure does not leave stale graph geometry. All connectors, including shared tool-return rails, use the same rounded corners. Shared tool-return rails carry no repeated labels; each outcome retains its continuation condition in the card. Distinguish model-selected tools from code branch conditions and nonbinding prompt guidance; an execution branch is not a prediction of the model's tool choice. Mark missing conditions as unmapped. Hide the capability next-step nudge while the trajectory is full screen. Keep the evaluation count toggle in the matching step card; expanding it reveals separate bordered evaluation rows floating beneath the card, with a visible gap and no enclosing panel. Rows show the name, Judge/Check type and stock provenance; selecting one opens its existing definition. Empty steps have a static zero count. Capability-entry rules retain their whole-run scope label; graph path expansion does not control evaluation disclosure. Rules with broad span scopes and rules without a unique source match remain separate. Configured evaluation counts remain separate from measured scores. The canvas run picker searches the complete capability trace history on the server with bounded 25-run pages and date and error filters. Use compact single-line run-ID and time rows in a narrow picker; do not add Latest run, follow-latest or map-view rows. No run is selected automatically. Selecting a new run pins that trace and enables highlighting; selecting the same run toggles highlighting. A matching toolbar route icon toggles highlighting without opening the picker, retaining the selected trace and its scores. Highlighting reveals recorded tools and outcomes and frames the captured route; turning it off restores the previous disclosures and the full map. Match recorded spans by code identity and unique source anchors or tool names, highlight visited steps and recorded connectors, and dim unvisited branches. Never infer uncaptured steps, concurrent call ordering or ambiguous conditions. A conditional outcome requires a named captured span beneath its matching tool; an unbranched result requires a captured tool output. Collapsed tool categories retain recorded execution context; score updates change styling and scores without rebuilding graph geometry or resetting the viewport. The observability icon button opens capability-filtered traces, including the selected trace when present. The card footer and floating rows show the selected run’s measured means and scored-rule counts. Load all score pages for that trace; use only the newest span receipt, excluding trajectory aggregates. Errors and abstentions stay distinct and never become numeric zeros; unmatched rules say Not scored. Score updates must not rebuild graph geometry or reset the viewport. Runtime-only expectations may not exist in the saved matrix.

## App shell

The left sidebar logic in `src/routes/_auth.tsx` and `src/components/ui/sidebar.tsx` is owned separately — pages adapt to the native in-flow sidebar. Don't add routes to focus-mode patterns or modify shell logic without explicit approval.

## Tooling

- Package manager is **Bun** (`bun add`, `bunx shadcn@latest add …`) — never pnpm/npm/yarn. New shadcn components get adapted to the token system after generation.
- **Biome must not format assets.** `.svg` is excluded in `biome.json` — keep it excluded and never run `biome … --write` over `**/*.svg` (2.5.x sorts attributes and corrupted a logo's XML prolog).
- Verify every UI change in the dark-only Console, including first paint with a previously saved light preference:

```bash
cd frontend
bun run typecheck && bun run lint
bun run check:all        # check:design + check:contrast + check:controls
```

Evaluation tables show the rule or authored criterion, its readable trajectory step or tool, its code-check/model-judge method and its recorded definition origin. Order capability rules by trajectory position before tools, and retain broad and unmapped scopes explicitly. Filter by agent part and assessment method; expose raw span identifiers and full definitions in row details. Enabled-only views do not repeat a green Enabled badge on every row.
