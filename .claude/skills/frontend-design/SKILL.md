---
name: frontend-design
description: Overmind Console design system — semantic tokens, shared primitives, geometry and icons, the border-contrast floor, the duplicated table implementations, and the verification scripts. Use when writing or changing frontend UI, styles, tables, or component markup.
---

# Frontend design conventions

The Console is "a quiet workshop for agent improvement" — warm minimalism, copper/gold brand, NeueBit (pixel, display + labels) and PP Neue Montreal (body), small radii, no shadows. Keep it; don't drift toward default Tailwind.

The token spec is the root `DESIGN.md` — extend its values, don't restructure it. Tokens are declared in `frontend/src/styles.css` under `@theme inline`.

## Color — semantic tokens only

- **Never** raw Tailwind palette utilities (`bg-green-500`, `text-blue-600`, `border-amber-400`) or hex in class strings (`bg-[#1c1917]`).
- **Status** → the four semantic tokens as a tint triplet. Tokens flip per theme, so **no `dark:` variants**: `border-success/40 bg-success/10 text-success`. Meanings: `success` = passed/healthy/connected/completed; `warning` = review/degraded/stale; `info` = running/pending/queued/training; `destructive` = failed/error/cancelled. Prefer the components — `<Badge variant="success|warning|info|error|neutral">` or `<StatusBadge status={domainStatus(state)}>` (`lib/job-status.tsx`, `lib/colors.ts`).
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

## Geometry, type, icons

- **No box-shadows** — every `--shadow-*` is `none`, so `shadow-*` utilities are dead no-ops. Depth = surface layering + 1px borders.
- Radii: `rounded-sm` for interactive primitives, `rounded-md` for containers. `rounded-lg/xl/2xl/3xl` are off-system.
- Headings use `font-display` (NeueBit, unweighted). `font-mono` is the loaded system stack — Fira Code is not shipped, don't reference it.
- Icons: **always the central registry** — `import { Icon } from "@/components/ui/icons"` → `<Icon.name className="size-4" />`. Never import `pixelarticons/react` or `lucide-react` in app code; both fail `check:design`. One concept = one glyph (`Icon.delete` = trash, `Icon.close`/`Icon.failed` = the "X"). Missing a glyph? Add one semantic entry to `ui/icons.ts`, the only file that may import the raw package. Sizes: `size-4` in buttons, `size-[17px]` in nav, `size-3` for dense chips.

## Two duplicated table implementations

A table fix applied to one codepath silently misses the other:

1. `src/components/ui/data-table.tsx` — the shared TanStack `<DataTable>` (Traces, Sessions, Runs, Datasets, Experiments, Billing)
1. `src/components/datasets/notebook/rows-grid.tsx` — the paged rows grid every dataset table (source, cell output, product) renders through

Sort headers are in `components/ui/sortable-header.tsx` for hand-built tables and inline inside `data-table.tsx` for the shared one — check both before declaring a header fix done.

## App shell

The left sidebar logic in `src/routes/_auth.tsx` and `src/components/ui/sidebar.tsx` is owned separately — pages adapt to the native in-flow sidebar. Don't add routes to focus-mode patterns or modify shell logic without explicit approval.

## Tooling

- Package manager is **Bun** (`bun add`, `bunx shadcn@latest add …`) — never pnpm/npm/yarn. New shadcn components get adapted to the token system after generation.
- **Biome must not format assets.** `.svg` is excluded in `biome.json` — keep it excluded and never run `biome … --write` over `**/*.svg` (2.5.x sorts attributes and corrupted a logo's XML prolog).
- Verify every UI change, in both light and dark:

```bash
cd frontend
bun run typecheck && bun run lint
bun run check:all        # check:design + check:contrast + check:controls
```
