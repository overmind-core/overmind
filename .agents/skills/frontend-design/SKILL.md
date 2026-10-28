---
name: frontend-design
description: Overmind Console design system — semantic tokens, shared primitives, geometry and icons, the border-contrast floor, the duplicated table implementations, canvas decisions and the verification scripts. Use when writing or changing frontend UI, styles, tables, or component markup.
---

# Frontend design conventions

The Console is "a quiet workshop for agent improvement": warm minimalism, a copper brand, small radii, flat surfaces. Geist Pixel is the body, card, section and popup face; PP Mondwest is only for main page titles; NeueBit is for navigation. Keep it; don't drift toward default Tailwind.

The token spec is the root `DESIGN.md`; extend its values, don't restructure it. The Console is dark-only: palette values live on `:root` in `frontend/src/styles.css`, exposed under `@theme inline`. First paint, authentication, toasts and flow canvases stay dark whatever the stored or OS preference. There is no theme selector, theme provider or preference listener.

## Color: semantic tokens only

- Use no raw Tailwind palette utilities (`bg-green-500`) and no hex in class strings (`bg-[#1c1917]`).
- **Status** uses a tint triplet, with no `dark:` variants: `border-success/40 bg-success/10 text-success`. The tones are `success` (passed, healthy, connected, completed), `warning` (review, degraded, stale), `info` (running, pending, queued, training), `error` (failed, cancelled; the `destructive` token) and `neutral` (draft, skipped, awaiting launch, unknown). Prefer `<Badge variant="success|warning|info|error|neutral">`; `variant="destructive"` is a solid fill, not the tint. For a domain state use `<StatusBadge status={state} fallback=…>`, which maps it through `domainStatus` itself (`lib/job-status.tsx`, `lib/colors.ts`).
- Active and queued job chips use `bg-foreground text-background` through the shared `StatusBadge`, not the blue info tint. Active chips pulse gently with reduced-motion support. Use the recorded coding-agent logo in place of the spinner when that task is attributed to a coding agent; never infer identity from connection presence.
- **Categorical** entity types use `text-cat-1 … text-cat-6` and `bg-cat-1/10`, one stable slot per type.
- **Login and onboarding** use the `--auth-*` tokens (`bg-auth-panel`, `border-auth-border`, `text-auth-accent`, …).
- Neutrals: `bg-background`, `bg-card`, `bg-muted`, `text-foreground`, `text-muted-foreground`, `border-border`.

## Border contrast

`--border` at full strength is only about 1.3:1 against a card; the visibility floor in `frontend/scripts/check-contrast.mjs` is 1.2. Use the sanctioned ramp: outline `border-border`, divider `border-border/70`, faint `border-border/60`. Anything below `/60` is invisible. Run `bun run check:contrast -- --all` before you lower a border opacity.

## Shared primitives

- Page title: `<PageHeader title description count actions icon meta/>`, never a raw `<h1>`. Page scaffold: `<PageShell variant="scroll|full" header/>`.
- Loading: `<LoadingState label fullPage/>` for initial pages, `<LoadingState label/>` for unknown-layout regions, `Skeleton` only for slots whose real dimensions are known. Keep loaded content mounted during background refreshes. Pending actions and live jobs use `<Spinner size/>`, never a hand-rolled `animate-spin`. See DESIGN.md § Loading and live updates.
- Empty state: `<EmptyState icon title description action size/>`. Titled card: `<SectionCard title …>`; otherwise `<Card>`. Don't hand-roll `rounded-lg border` surfaces.
- Destructive confirm: `<ConfirmDialog destructive onConfirm/>`, never `window.confirm`. Dialog Cancel is `variant="secondary"`.
- Feedback: `sonner` toast for a transient async result, `Alert` for a persistent inline notice, `DismissibleAlert` for an in-dialog mutation error.
- Model choices use `ModelOptionLabel` (provider logo, model name, then metadata) in dropdown rows and selected values. `ModelProviderChip` is for read-only badges. Model IDs stay the stored values.

## Geometry, type, icons

- Flat surfaces: `shadow-*` utilities are disabled. Depth is surface layering plus 1px borders. Shared confirmation dialogs alone use `shadow-confirmation`.
- Radii: `rounded-sm` for interactive primitives, `rounded-md` for containers, `rounded-xs` only for elements smaller than about 8px. `rounded-lg` and larger are off-system.
- Main page titles use `TITLE.page` (Mondwest); every other heading uses its `TITLE` role in Geist Pixel. `font-mono` also resolves to Geist Pixel.
- Icons come only from `import { Icon } from "@/components/ui/icons"` → `<Icon.name className="size-4" />`. The pixelarticons paths are vendored in `ui/icons/glyphs.ts`; app code never imports it or `lucide-react` (`check:design` fails). One concept, one glyph. Provider logos are the exception: `model-provider-chip.tsx` imports `@lobehub/icons`. To add a glyph, add `name: glyph("<svg-name>")` to `ui/icons/index.ts`, then run `bun run icons:vendor` with `PIXELARTICONS_LICENSE_KEY` set (or `--source <svg dir>`). Sizes: `size-4` in buttons, `size-[17px]` in nav, `size-3` in dense chips.

## Two table implementations

A table fix applied to one misses the other:

1. `src/components/ui/data-table.tsx` — the shared TanStack `<DataTable>` (Traces, Sessions, Runs, Datasets, Experiments, Billing), with inline sort headers.
1. `src/components/datasets/notebook/rows-grid.tsx` — the paged rows grid every dataset table renders through. Hand-built tables use `components/ui/sortable-header.tsx`.

Eval results align model variants by a sample key (`components/evaluations/generated-run-results.tsx`): the cell row index (`metadata.legacy_sample.row_index` first, then the generated client's `rowIndex`, including zero) when the run has a cell, else `sourceTraceId`, together with input and reference; samples with neither keep their own identity. Unrelated outputs never collapse or stand in for another model's missing response.

Evaluation tables show the rule, its trajectory step or tool, its method (code check or model judge) and its definition origin. Enabled-only views don't repeat an Enabled badge on every row.

## Datapoint popups

- Generative: one model at a time, its input beside its output, reference and judge reasoning. Never label a combined trajectory as input or an extracted prediction as the full output.
- Native probability: shared frozen input on the left and every model's output in one scrolling list on the right, with no model tabs. Narrow screens stack them.

## Canvas decisions

These apply to the capability trajectory canvas (`components/capability-detail/flow/`). They are decisions, not defaults; keep them:

- Tools and outcomes are separate canvas nodes, never nested cards. Nodes have no detail sidebar and no clickable header.
- No separate execution overview, action-only navigator, top toolbar or legend. Search is a popover in the canvas action dock beside expand-all and collapse-all.
- Never infer a destination from continuation prose, uncaptured steps, concurrent call order or ambiguous conditions. Missing conditions show as unmapped. Dashed action edges are available choices, not observed execution.
- The run picker searches the full trace history on the server in 25-run pages. No "Latest run" or follow-latest rows, and no automatic selection.
- Score updates restyle nodes without rebuilding geometry or resetting the viewport. Errors and abstentions never become zeros; unmatched rules say Not scored.
- Every canvas action uses the shared tooltip; a native `title` is not a substitute.
- The Workshop canvas rules are in the data-workshop skill.

## App shell

The sidebar logic in `src/routes/_auth.tsx` and `src/components/ui/sidebar.tsx` has a separate owner. Pages adapt to the in-flow sidebar. Don't add routes to focus-mode patterns or change shell logic without explicit approval.

## Tooling

- Bun only (`bun add`, `bunx shadcn@latest add …`). Adapt generated shadcn components to the tokens.
- Biome must not format assets: `.svg` is excluded in `biome.json`, because biome sorts SVG attributes and once corrupted a logo's XML prolog.
- Verify every UI change in the dark Console, including first paint with a saved light preference:

```bash
cd frontend
bun run typecheck && bun run lint
bun run check:all        # check:design + check:contrast + check:controls
```
