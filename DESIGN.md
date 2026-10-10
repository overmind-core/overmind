---
name: Overmind Console
description: A quiet workshop for agent improvement — warm neutral, flat, pixel-first.
colors:
  background: "#0e0c09"
  foreground: "#f6f3ef"
  card: "#151311"
  popover: "#1e1c1a"
  primary: "#f6f3ef"
  primary-foreground: "#181614"
  secondary: "#1a1816"
  control: "#262320"
  control-hover: "#2f2c29"
  muted: "#191715"
  muted-foreground: "#a9a39c"
  accent: "#201e1b"
  border: "#3a3633"
  input: "#6a645d"
  ring: "#f6f3ef"
  destructive: "#f65d57"
  success: "#3ac28a"
  warning: "#e6a647"
  info: "#6fa8dc"
  cat-1: "#6fa8dc"
  cat-2: "#4cc59a"
  cat-3: "#5fc3cf"
  cat-4: "#b29ae8"
  cat-5: "#e08abf"
  cat-6: "#a4988b"
  instrument-wall: "#0e0c09"
  instrument-panel: "#151311"
  instrument-panel-edge: "#3a3633"
  instrument-inset: "#0e0c09"
  instrument-bar-unlit: "#2f2c29"
  instrument-healthy: "#3ac28a"
  instrument-caution: "#e6a647"
  instrument-blocked: "#f65d57"
  instrument-analysing: "#6fa8dc"
  instrument-stale: "#a9a39c"
  brand-copper: "#ed670f"
typography:
  display:
    fontFamily: "PP Mondwest, system-ui, sans-serif"
    fontSize: "2.25rem"
    fontWeight: 700
    lineHeight: "2.5rem"
    letterSpacing: "-0.015em"
  headline:
    fontFamily: "PP Mondwest, system-ui, sans-serif"
    fontSize: "1.8225rem"
    fontWeight: 700
    lineHeight: "2.025rem"
    letterSpacing: "-0.015em"
  title:
    fontFamily: "PP Mondwest, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: "1.75rem"
    letterSpacing: "-0.015em"
  title-card:
    fontFamily: "PP Mondwest, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: "1.5rem"
    letterSpacing: "-0.015em"
  wordmark:
    fontFamily: "PP Mondwest, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 400
    lineHeight: "1"
    letterSpacing: "0.025em"
  body:
    fontFamily: "Geist Pixel, system-ui, -apple-system, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: "1.25rem"
    letterSpacing: "normal"
  label:
    fontFamily: "Geist Pixel, system-ui, -apple-system, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: "1"
    letterSpacing: "0.038em"
  sidebar:
    fontFamily: "NeueBit, system-ui, sans-serif"
    fontSize: "1.46em"
    fontWeight: 400
    lineHeight: "1"
    letterSpacing: "0.05em"
  prose:
    fontFamily: "Geist Pixel, system-ui, -apple-system, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: "1.5rem"
    letterSpacing: "normal"
rounded:
  DEFAULT: "2px"
  xs: "1px"
  sm: "2px"
  md: "3px"
spacing:
  base: "0.25rem"
  card-padding: "28px"
  section-header: "10px 16px"
  section-body: "16px"
  dialog: "16px 20px"
  compact: "10px 12px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  button-primary-hover:
    backgroundColor: "color-mix(in srgb, #f6f3ef 85%, transparent)"
    textColor: "{colors.primary-foreground}"
  button-secondary:
    backgroundColor: "{colors.control}"
    textColor: "{colors.foreground}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  button-secondary-hover:
    backgroundColor: "{colors.control-hover}"
  button-outline:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    rounded: "{rounded.sm}"
    size: "32px"
  button-destructive:
    backgroundColor: "{colors.destructive}"
    textColor: "#0c0a08"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  input:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "32px"
  input-focus:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
  card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.md}"
    padding: "{spacing.card-padding}"
  badge-success:
    backgroundColor: "color-mix(in srgb, #3ac28a 10%, transparent)"
    textColor: "{colors.success}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  badge-neutral:
    backgroundColor: "#211f1c"
    textColor: "{colors.muted-foreground}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  sidebar-item-active:
    backgroundColor: "color-mix(in srgb, #f6f3ef 8%, transparent)"
    textColor: "{colors.foreground}"
    typography: "{typography.sidebar}"
    rounded: "{rounded.sm}"
---

# Overmind Console design system

## Overview

The console is a quiet, dark-only workshop. The canvas is warm near-black;
the ink is warm near-white. Nothing is lifted,
nothing glows, nothing bounces. An engineer comes here to read production
facts about their agent and leave with a decision, so the interface holds
still and the data is the most prominent thing on screen.

Two voices are set against each other on purpose: display typography on top
of a pixel terminal face. Titles are set in PP Mondwest. Every label, table
cell, chip, metric, paragraph and button is set in Geist Pixel. The result is
a CRT-era workshop typeset with care: warm where developer tools are usually
cold, and pixel-sharp where SaaS consoles are usually soft.

The constraints are the design. Standard shadow tokens resolve to `none`;
depth comes from tonal layering and hairline borders. Confirmation dialogs
alone use a subtle drop shadow. Corners top out at 3px. Colour is rationed: the copper
brand mark is the only saturated element in the product, and orange anywhere
else means warning. `check:design`, `check:contrast` and `check:controls` fail
the build on a violation.

In short:

- Warm neutral throughout: near-black canvas, near-white ink, muted warm borders.
- Flat surfaces, with a subtle drop shadow reserved for confirmation dialogs.
- Pixel geometry: a 1px / 2px / 3px radius ramp, including the Workshop bezel and composers.
- Mondwest for titles, Geist Pixel for everything else, NeueBit for navigation.
- Sentence case everywhere; uppercase and wide tracking are banned.
- The copper eye carries the brand, so nothing else has to.
- Dark is the only theme, from first paint through every product surface.
- The guardrails are scripts, not conventions.

## Colours

A single warm-neutral dark palette, with saturation reserved almost entirely
for state. Stored theme choices and operating-system appearance do not change it.

### Primary

- **Workshop Ink** (`#f6f3ef`): Primary text, primary button fill and focus ring.
  The primary foreground is the warm near-black `#181614`.

### Secondary

- **Control Face** (`#262320`): The neutral control fill, distinct from both
  card and canvas. Hover steps to `#2f2c29`.

### Tertiary

- **Instrument Panel** (`#151311` on `#0e0c09`): The Workshop uses the same
  opaque layered surfaces and semantic state colours as the rest of the Console.

### Neutral

- **Warm Canvas** (`#0e0c09`): Near-black with a warm undertone.
- **Wall** (`#080604`): Sidebar, one step below the canvas.
- **Card** (`#151311`): Raised surface; popovers step to `#1e1c1a`.
- **Muted / Secondary** (`#191715` / `#1a1816`): Distinct neutral fills.
- **Hover Wash** (`#201e1b`): Neutral hover feedback, not a warning hue.
- **Quiet Ink** (`#a9a39c`): Secondary text and default icon colour.
- **Border** (`#3a3633`): Outlines and dividers at the three sanctioned levels.

### The elevation wash

Two steps and only two: `bg-wash-subtle` and `bg-wash-raised`.

| Token           | Colour    | ΔL\* from card |
| --------------- | --------- | -------------- |
| `--wash-subtle` | `#1b1917` | +2.9           |
| `--wash-raised` | `#211f1c` | +5.9           |

### Status

One status vocabulary, shared by every badge, alert, chip and instrument
readout, using semantic tokens without `dark:` variants:

- **Field Green** (`#3ac28a`): success
- **Burnt Amber** (`#e6a647`): warning
- **Steel Blue** (`#6fa8dc`): info
- **Brick Red** (`#f65d57`): destructive and error.
  Muted, never pure red. The value sits at the same L\* band as its three
  siblings (67 to 73), so error does not read hotter than the other states.

### Categorical

Six fixed qualitative hues (`cat-1` to `cat-6`: steel blue, teal-green, cyan,
violet, plum, warm grey) for charts that distinguish categories. They are a
fixed set, never hash-derived per render, so a given slot is always the same
colour across every chart.

### Named rules

**The Copper Eye rule.** The brand copper exists in exactly one place:
`overmind-eye-copper.svg`. It is not a token, not a class, not a chart hue, not a
gradient stop. Orange in the UI means warning, and warning only. `check:design`
greps for the copper hex family and fails the build if it appears anywhere
outside the logo asset.

**The One Registry rule.** No component names a colour. Components ask for a
meaning ("this score is poor", "this job failed", "this is series 3") and
`lib/colors.ts` returns classes. Hex literals, `hsl()`, `rgb()` and raw Tailwind
palette classes (`bg-red-500`, `text-zinc-400`) are all forbidden in app code.
There is one copy of every colour decision.

**The Tint Triplet rule.** Status colour is always the same three-part idiom:
`border-{tone}/40 bg-{tone}/10 text-{tone}`. A bare element styled this way and a
`<Badge>` are visually identical by construction. The `-foreground` pairs exist
only for the rare solid fill.

**The One Hue Axis rule.** Every neutral sits on h≈75° in
CIELCh, at a chroma held under a ceiling of 6 so it cannot run away as the value
darkens. Chroma may rise down the ramp, but the ceiling is the enforced part. A white card against a C=10
tan makes the tan read as a stain and the white read clinical, and a canvas on
a cool hue contradicts the warm undertone. `check:contrast` asserts the chroma
ceiling and the hue band.

**The Tier Is Measured In L\* rule.** Two adjacent surfaces are separated by
perceived lightness, floor ΔL\* 1.2, never by contrast ratio. Ratio compresses
at the dark end of the curve: `--card` against `--background` is 1.06:1, a number that says nothing about whether the eye reads one
surface or two. A contrast table can run green for a palette whose tiers are
byte-identical. The sidebar is the one tier allowed to separate either by tone
or by a visible `--sidebar-border`; the palette carries both. Neither is banned,
because then the shell has no left edge at any level.

**The Wash Ladder rule.** Elevation is `bg-wash-subtle` / `bg-wash-raised` and
nothing else. `bg-muted/N` is banned by `check:design`: alpha steps of 0.25 to
0.5 L\* fall under the ~1 L\* just-noticeable difference and render as
nothing. Two steps is the whole ladder.

**The Contrast Is Checked rule.** Colour decisions are easy one at a time and
impossible to hold in your head as a set. `bun run check:contrast` parses the dark-only
root tokens out of `styles.css` and asserts WCAG 2.1 AA on every pairing the UI
renders: 4.5:1 body text, 3.0:1 large text and non-text UI. Adding a token means
running it.

## Typography

Display font: PP Mondwest (fallback `system-ui, sans-serif`).
Body and prose font: Geist Pixel Square (fallback `system-ui, -apple-system, sans-serif`).
Navigation accent: NeueBit (fallback `system-ui, sans-serif`).

Three self-hosted faces with three non-overlapping jobs. Mondwest appears only
on titles and gives page headers a presence a SaaS console does not usually
have. Geist Pixel is the platform default: set on `body`, so a label, table
cell, chip, button, metric or paragraph needs no class at all. NeueBit is the
navigation accent, scoped to two surfaces, the sidebar nav rows and the
breadcrumb, so "you are here" is the one thing on screen in a different voice.

Prose is a role, not a second typeface. `--font-prose` resolves to the same
family as `--font-sans`; it stays a distinct token so reading surfaces remain
greppable and can diverge again without touching call sites.

**What this face costs.** Geist Pixel ships one 400 master, so every
`font-bold` and every markdown `<strong>` is browser-synthesized. Do not "fix"
this by declaring a `font-weight: 400 700` range on the `@font-face`: it
suppresses synthesis and renders bold identical to regular, deleting emphasis
product-wide. NeueBit's real Bold cut is the only true weight left, on the
active sidebar row. `--font-mono` points at Geist Pixel too and is therefore not
monospaced: advances are proportional and it ships no `tnum`, so `tabular-nums`
is inert and nothing set in it aligns into columns.

**The grid.** Every advance in the face is quantised to a 38/1000em cell. That
makes `--tracking-cell` (0.038em) the only legal tracking step; anything else
lands glyphs between cells and the stems stop lining up. Labels and chips use
it; prose resets to `normal`.

### Hierarchy

Titles pick exactly one of four steps. Size and face are the only hierarchy
levers; every step shares the same weight.

- **Display / `title-hero`** (700, 36px, 40px line): Hero and empty-state
  headlines. At most one per screen.
- **Headline / `page-title`** (700, ~29px, 32.4px line): The single page header.
  Use the `PageHeader` component; never hand-roll it.
- **Title / `title-section`** (700, 20px, 28px line): A section heading within a
  page.
- **Title-card / `title-card`** (700, 18px, 24px line): Card, dialog, sheet and
  alert titles.
- **Body** (400, 14px): The dominant UI size. The tokenized ramp is Tailwind's
  unmodified default: xs 12, sm 14, base 16, lg 18, xl 20, 2xl 24, 4xl 36.
- **Label** (400, 12px, +0.038em): Eyebrows, table column headers, tab labels
  and stat captions, via `.pixel-label`. Regular weight on purpose: labels read
  as chrome, not emphasis.
- **Sidebar** (400, 1.46em of the parent, +0.05em): Navigation and breadcrumb
  labels, the NeueBit accent, and nowhere else. The active row steps to a
  genuine 700 cut. NeueBit sizes itself by hand because its caps are half its
  declared size.
- **Prose** (400, inherits size, normal tracking): Markdown, chat and assistant
  messages, score reasoning, long descriptions, empty-state subcopy.

### Named rules

**The Three Faces rule.** Mondwest for titles, NeueBit for navigation, Geist
Pixel for everything else. "Everything else" is the default and needs no class. A
call site never names a font family; it imports a role from `lib/typography.ts`
(`TITLE.page`, `PROSE`, `LABEL.chip`) so the set of surfaces using each face
stays greppable. Inline `fontFamily` is banned outright, with no allowlist.

**The Accent Stays Scoped rule.** NeueBit means "navigation" and nothing else.
`check:design` fails any `font-sidebar` or `--font-sidebar` outside
`app-sidebar.tsx`, `routes/_auth.tsx` and the two registries. A face keeps its
meaning by being the only thing that has that job.

**The Size Is Hierarchy rule.** All four title steps share one weight, because
Mondwest ships a Regular master only and a weight utility renders synthesized
bold. Pairing a `text-*` size with a title class is banned too; that is how four
steps collapse back into a dozen ad-hoc sizes.

**The Sentence Case rule.** Every label, chip, badge, tab and column header is
sentence case with acronyms preserved, via `sentenceCase` / `humanizeKey` in
`lib/label-case.ts`. The `uppercase` utility is banned outright: it hides the
authored casing, so the string underneath drifts and the label reads as an alarm
on screen. Wide tracking is banned with it; it existed only to space all-caps
labels, and on sentence case it reads as a gap.

**The Is It A Sentence test.** Before reaching for prose: would you read this
as a sentence, or scan it as a value? A label, slug, code span, table cell,
chip or single word is not prose and stays on the default. Prose sets ~16%
wider than a grotesque, so a paragraph runs about one extra line per three.
Never size a prose container to a line count.

**The Floor Is `text-xs` rule.** 12px is the smallest step. Arbitrary
`text-[Npx]` is banned. A pixel face has a real floor: at 12px the x-height has
too few device pixels to resolve crisply, so dense read-heavy panels (span
details) want `sm`, not `xs`.

## Layout

The MCP connection consent page uses the website's 404 starfield, trees and
asteroid artwork. It stays dark within its own root, with a regular-weight
Mondwest display heading and a solid permissions panel inside a pixel frame.
Artwork is decorative and noninteractive; reduced-motion preferences stop the
asteroids. The page scrolls independently so consent controls remain reachable
on short screens.

The authenticated shell is the structural template: the entire application
interior is one card in an 8px warm gutter.

```
SidebarProvider
└─ AppSidebar (16rem expanded · 3rem icon-collapsed · 18rem mobile)
└─ SidebarInset (bg-background, p-2)
   └─ inner card (rounded-md border border-border bg-card)
      ├─ header h-14 · border-b · [Toggle] [Breadcrumb … ProjectSelector] [Settings]
      └─ scroll area p-4 md:p-6 → <Outlet />
```

Spacing runs on Tailwind's default `0.25rem` unit, with steps of 1.5 / 2 / 3 / 4
/ 6 / 7 dominating. Cards pad wider than shadcn defaults: `p-7` header,
`px-7 pb-7 pt-0` content.

Detail surfaces built from stacked, header-stripped cards use one padding scale.
Pick a row in this table before inventing a value. The failure mode is a page
where every card pads differently and the internal dividers do not line up.

| Slot                                     | Padding                                                |
| ---------------------------------------- | ------------------------------------------------------ |
| Header strip (single-line label)         | `px-4 py-2.5`, `border-b border-border/60`, muted fill |
| Card header (title and description)      | `px-4 py-3`, `border-b border-border/60`, no fill      |
| Card body                                | `p-4` (`px-4 py-3` when a single compact row)          |
| Dialog header / body                     | `px-5 py-4`                                            |
| Compact card (canvas node, chip cluster) | `px-3 py-2.5` / `p-3`                                  |
| Nested `pre` / code block                | `p-3`                                                  |

Rhythm: tab content stacks at `space-y-4`; blocks inside a card at `space-y-3`;
label and value pairs at `space-y-1.5`; definition grids
(`grid-cols-[max-content_minmax(0,1fr)]`) at `gap-x-4 gap-y-2`. The page wrapper
utility is `space-y-6 pb-8`. Card grids are
`grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4`.

Responsive behaviour is modest by design: content padding scales `p-4` to `p-6`
at `md`, card grids collapse to one column, and the sidebar becomes an 18rem
sheet. This is a desktop-first instrument.

### Named rules

**The Matched Line Box rule.** Mixed icon/badge/text rows align by giving the
icon a wrapper whose height equals the text's line box, then using `items-start`,
never by hand-tuned nudges. `mt-px`, `pt-0.5`, `align-[-1px]` and
`self-center` on an already-centred parent are all smells: if you need one, the
line boxes disagree, and that is the actual bug. Use `items-center` rather than
`items-baseline` whenever a boxed token sits beside plain text, or the box hangs
low while its glyphs align.

**The Three Border Levels rule.** `border-border` outlines a card or panel,
`border-border/70` draws an internal divider, `border-border/60` is a faint rule.
Three levels, and nothing between them.

The levels start at full strength because this token has no headroom below it.
`--border` against a card is 1.32:1, and `check:contrast` floors a border at
1.20, the point at which it is visible at all. A 70/60/40 ramp lands at 1.19 /
1.16 / 1.09, so its middle and bottom steps sit under the floor and any faint
rule written to it disappears. An outline is therefore the bare token, and the
two quieter steps spend the only 0.12 of contrast there is to spend. Do not add
a fourth.

## Elevation and depth

Standard shadow tokens resolve to `none`, so `shadow-lg`
is dead code, and `check:design` flags it as such. Confirmation dialogs are
the user-requested exception: `shadow-confirmation` uses the palette-matched
`--elevation-confirmation` token, with a faint contact shadow and a soft,
downward cast. No other surface uses it.

Confirmations follow the shared small-dialog frame: background surface, a 26rem
natural width within the 75vw/75vh ceiling, 20px horizontal insets, a 16px header
inset and a 12px footer inset. Header and footer rules span the frame. Titles
and descriptions use the shared typography and compact spacing. Actions use
standard button variants, including the filled destructive button and its
palette-matched foreground; they wrap only when needed. Cancel keeps initial focus.

Depth is carried three ways instead. Tonal layering steps from
background `#0e0c09` to card `#151311` to popover `#1e1c1a`. Hairline borders: a 1px sand rule does the
work an elevation shadow would. Inset edges: the workshop console draws its
panel border with `box-shadow: inset 0 0 0 1px` rather than `border`, so the
edge sits inside the layout box and never shifts child positions.

### Named rules

**The Flat Room rule.** Selection, elevation and emphasis read through border,
ring and background tone. If a surface needs to read as closer, change its tone
or its edge. Only confirmation dialogs use the named shadow token; never add
a scale transform or hover lift.

## Shapes

Pixels only. A corner exists to stop an edge reading as a hard cut, not to
soften the shape. Three steps, and nothing above them:

- **`rounded-xs` (1px)**: anything under ~8px: status dots, progress bars,
  switch, slider.
- **`rounded-sm` (2px)**: everything interactive: buttons, inputs, chips,
  badges, tabs, menu rows.
- **`rounded-md` (3px)**: containers only: cards, dialogs, popovers, sheets,
  menus, and the app shell itself.

The ramp is by size, not importance. An 8px dot at 2px radius is a third
round; a card at 2px is barely nicked. Small things take a smaller corner to look
equally sharp. A status dot is a rounded square, a progress bar has squared ends,
and the switch is a rectangle.

The pixel logic extends past radius. The sidebar's sub-item connector is drawn as
a stepped staircase in SVG mask data rather than a smooth elbow. Icons are
pixelarticons glyphs vendored as path data. The cursor is a custom pixel-art SVG in three states
(default, pointer, active), enforced globally. The workshop console adds a
diagonal pinstripe hatch for stale blocks and a low-alpha grain overlay.

### Named rules

**The No Uncapped Alias rule.** `rounded-lg`, `rounded-xl` and `rounded-full` do
not exist and are not aliased to something smaller on purpose. An alias lets an
uncapped name keep appearing in code while quietly meaning 3px, and the next
person to read it believes the name. Bare `rounded` is banned for the same
reason: it falls through to Tailwind's own `0.25rem`, a radius no token
controls. Arbitrary radii (`rounded-[2px]`) are the token's value written where
the token can't reach it.

## Components

### Buttons

- **Shape:** Barely nicked corners (2px), 150ms colour-only transition.
- **Height ramp:** One shared control ramp: `xs` 24px, `sm` 28px, default 32px,
  `lg` 36px. `xs` is the notebook cell's scale: every bar in a cell is 32px.
  A row of controls stays aligned by giving them all the same `size`, never by
  patching `h-*` onto one.
- **Primary:** Ink fill, canvas text (`bg-primary text-primary-foreground`),
  hover to 85% ink.
- **Secondary:** The neutral control face: a soft `#efe4d7` fill and no
  border, hovering to `#e5d7c7`. An outline on every control reads cheap at this
  density, and the fill still makes the button obvious at rest.
- **Outline:** A true hairline, reserved for controls that must read against an
  already-filled surface.
- **Ghost:** Icon-only affordances only: a close ×, a disclosure chevron, a
  row action. A ghost button carrying a label reads as body text, so a labelled
  button always takes a filled variant. `check:controls` enforces this.
- **Focus:** 2px ring at 60% of `--ring`. No shadow, ever.
- **Icon spacing is the primitive's job.** Every size sets its own `gap`; a call
  site must not add `mr-*` to the icon, which stacks on the gap and makes the
  same button render at different widths.

### Inputs / fields

- **Style:** Transparent fill with a 1px `--input` border, 2px radius, matching
  the 28/32/36px control ramp.
- **Focus:** Border shifts to `--ring` plus a 2px ring at 40%.
- **Error:** `aria-invalid:border-destructive` only. No glow, no error pill by
  default.

### Cards / containers

- **Corner:** 3px. **Background:** `--card`. **Border:** `border-border/80`.
- **Shadow:** None. See Elevation and depth.
- **Padding:** `p-7` header, `px-7 pb-7 pt-0` content. Internal separation uses
  `divide-y divide-border/40` rather than padded blocks.
- **Title:** Always `TITLE.card` (18px Mondwest).

### Badges / chips

The tinted triplet is the app's one chip idiom:
`border-{tone}/40 bg-{tone}/10 text-{tone}` across success, warning, info, error
and neutral. Neutral tints from `--muted-foreground/50` rather than `--border`:
the border token is sized for long dividers and lands at 1.25:1 against the
chip's own fill, so the edge vanishes and the chip reads as bare text. Coloured
tones get away with a 1.65:1 border because their hue does the work.

### Navigation

- **Sidebar:** 16rem expanded, 3rem icon-collapsed, 18rem mobile. Labels in
  NeueBit at 1.46em with +0.05em tracking. Transparent border and accent; the
  separation comes from background tone alone.
- **Active row:** 8% foreground tint, a genuine 700 bitmap cut, foreground-tinted
  icon, 2px radius.
- **Sub-items:** Connected by the stepped pixel elbow described in Shapes.
- **Breadcrumbs:** Chevron-separated, NeueBit, active crumb bold, inactive
  `text-muted-foreground hover:text-foreground`.

### Icons

Every glyph is a `pixelarticons` path vendored into
`frontend/src/components/ui/icons/glyphs.ts` and rendered through the `Icon`
registry, so one concept maps to exactly one glyph and a clone needs no icon
package or licence key. The full ~4,400-glyph pack is available, so pick the
glyph that means the thing: a new one is `name: glyph("<svg-name>")` in the
registry plus one run of `bun run icons:vendor`, which needs
`PIXELARTICONS_LICENSE_KEY` (or `--source` pointing at an unlocked package).

Three sizes, by context:

- **`size-4` (16px)**: inside buttons and most inline affordances.
- **`size-[17px]`**: sidebar and top-level navigation.
- **`size-3` (12px)**: dense and metric contexts, and inside `xs` buttons.

The brand eye renders in `currentColor` so it takes the sidebar foreground.
Page-title icons are local two-tone wrappers (ink plus white) in
`ui/icons/page-title.tsx`. The pixel monitor marking the Agents section renders with
`[image-rendering:pixelated]`.

### Scrollbars

An overlay scrollbar: fully transparent at rest, painted only while the element
is scrolling (`data-scrolling`, stamped by `lib/autohide-scrollbars`) or while
the pointer is on the bar. The thumb derives from `--foreground` via `color-mix`
so it shares the surface palette, inset by a 3px transparent border so it reads as a
slim pill. The gutter is always reserved, so appearing and disappearing never
reflows content.

### Data Workshop notebook

Self-hosted login uses the email/password card. There are no ChatGPT connection,
funding or model-selection controls in login, Settings or Data Workshop.

The Workshop landing page is a searchable, filterable dataset table with a page
heading and project count. Dataset listings and their empty states have no New
dataset action. Columns show name, intent,
active version, row count, source, capability and last update. Intent uses a
monochrome outlined chip, not a status tint. Selecting a row
opens the existing cell-based notebook. There is no chat-style landing composer.

Dataset navigation and content share a continuous outer bezel (3px). The 248px
column starts collapsed, including on landing. The notebook folder button reveals
only the selected project's datasets, with search and paginated loading. There
is no separate navigation header; mobile keeps a close control beside search.
Desktop dataset rows are 28px with no vertical gaps; mobile
rows are 36px and keep actions visible. Selected rows use the accent surface.
Names truncate with full names and source/row facts in hover/focus details. The
column uses the existing grid animation with reduced-motion support. Closed
navigation is inert and stops polling. Mobile temporarily hides the mounted
notebook while navigation is open; closing it returns to the same cells.

Dataset creation uses the medium 40rem dialog frame and training setup's compact
field labels. Selected traces appear once beneath the title. The name leads,
capability and purpose share a row, and a two-line Intent field is explicitly
optional with an example placeholder. Split controls appear only for Train + eval;
their row counts stay inline. On narrow screens, fields stack and only the body scrolls.

The dataset page retains its existing cells at their original notebook width and
scale 1. The grid-snapped canvas progresses downward; sibling branches share a
horizontal layer beneath their parent. Square elbow connectors show recorded
lineage without fixed end stubs that can double back in narrow layer gaps.
Connector ports are centered on visible cell-frame border strokes, not the outer
padded box. That outer spacing stays transparent so it cannot cover edge paths.
Only connected inputs and outputs show a port.
Condition labels use card fill, a 1px neutral outline and 2px corners.
Cells open at full scale; zoom and Fit view are explicit camera controls, not
changes to cell dimensions. Reset and linked-cell focus return to scale 1, while resize
preserves the chosen zoom. The Agent trajectory's minimap treatment is available
through the left-hand controls, hidden initially and remembered in this browser.
A version chip beside the dataset name selects and restores
iterations without a separate run bar.
Live cell arrivals receive a bounded opacity reveal after graph placement; changed
content or state briefly washes the existing frame with a neutral surface token.
Cells never move independently of their connectors. Initial load, unchanged
refreshes, usage metadata and version browsing remain quiet. Reduced motion uses
a shorter, gentler acknowledgement; background/offscreen updates do not replay.
There is no platform chat or bottom chat composer. Dataset metadata changes use
shared lifecycle endpoints. The rail on the left groups its controls in a compact,
opaque card-colored box with a 1px neutral border and small corners. It holds
a folder button that expands or collapses the existing workspace column and,
when cells exist, the minimap toggle. There is no cell-search button or cell
selector strip. The folder changes to its open state while expanded.
The sidebar closes on workspace selection or leaving the route; toggling it
preserves the notebook. It uses the existing grid animation,
with no separate pane, dialog or scrim.

Each cell is a rounded frame with a chip on its top border. The chip is
sticky: while a cell scrolls through the column its chip stays at the top and
the next cell's chip pushes it away. The chip holds the version, read-only title,
Export (CSV or JSONL), state and a lock when frozen. The active cell draws its
frame and its chip at 1.5px in the `success` tone with a small green square
before the version. Inside, every bar is 32px with `xs` controls: the two
collapsible strips with the same chevron header, read-only **Script** when present and **Data**
(`rows × columns` in its meta), the table's search and filter row, and its
pagination. The frame reserves 12px above its content for the overlapping title
chip, including cells with only Data and no Source or Script section. Data has
a top divider only when another section precedes it.
Cells have no Execution details section; durable run facts remain
available through the version selector and MCP. The table shows new rows on `success/10` and changed values as a
word-level diff in place: removed on `destructive/15` struck through, added on
`success/20`. A click opens the row in place: the same cells stop truncating
and wrap to their full text (objects as indented JSON), and a second click
closes it. No extra row, no sheet, no per-type rendering.

The rows search field and Filter button form one connected control with matching
24px heights, outer corners and a single shared border. Their shared baseline does
not depend on inline-input line boxes; the existing filter glyph and behavior stay
unchanged. Active filter and sort chips remain outside this group.

Source cells show a compact row of outlined source chips, not a collapsible text
section. File chips show the filename, row count and original-file download;
long filenames truncate with their full name available on hover. Other sources
show their recorded type, including derived datasets. The brief, checksums and
extraction commentary stay out of this cell summary; retained provenance remains
available through MCP.

The footer has a 32px minimum height and wraps at narrow widths. First an **Active** indicator (`success` chip
with a tick). Then a contract chip that names the first failure (**Not a
train table**, **Does not match Research Agent**, **Intent pending**, else
**Fits**). Its hover card gives each contract as Needs / Columns / Found / Fix
and allows direct intent/capability changes where the dataset is not frozen. There
is no automatic repair action; transformations are authored by the native coding agent. Then the
consumers in the primary tint on the right: a train table offers **Train a
model**; an eval table offers **Run the optimiser** and **Use in a training
job** (the eval dataset that scores the job); each opens the wizard on its
page with this version chosen. Any other version that ran shows **Set active**
as a plain outline on the right. A failed cell prints its traceback under an
**Error** label.

The quality chip shows **Quality passed** or a warning-tinted **Review recommended**.
Its hover card lists measured checks and remaining work. Quality and capability
findings never disable consumer buttons. Review recommendations remain in the
workshop, not training setup. Setup has no per-model preprocessing cards or
preprocessing gate; model-specific preparation runs within the launched job.
Overlap is advisory in setup.
Unreadable data, invalid technical formats and incompatible model settings still
block execution. There is no extra quality-approval step.

### Motion (system-wide)

150ms for colour and background transitions, 200ms for dialog open/close, `ease`
and `ease-out` throughout. No spring physics, no bounce. Hover changes colour,
background and border only: never scale, never lift, never shadow.

## Language

Every word a user reads is British English. Labels, buttons, headings, empty
states, toasts, error messages, tooltips, placeholder text, chart axes, aria
labels, page titles, and product docs.

- `-ise` / `-isation`, not `-ize` / `-ization`: optimise, normalise, summarise,
  initialise, organisation.
- `analyse`, `analyser`, `analysing`, `analysed`. `analysis` and `analyses` are
  already correct.
- `-our`: colour, behaviour, favourite.
- `-re`: centre, metre. A gauge or measuring device stays a `meter`.
- `-ence` for the noun: licence, defence. The verb "to license" keeps its `s`.
- `catalogue`, `artefact`, `grey`, `cancelled`, `labelled`, `towards`,
  `programme` (a computer program stays a `program`).
- `dialog` stays `dialog`: the UI element, not a conversation.

Code is exempt and stays as it is. Identifiers, CSS properties and class names
(`items-center`, `transition-colors`, `--color-*`), DOM and library APIs
(`scrollIntoView({ behavior })`, Radix `Dialog`), API routes, query keys, OTel
attributes, status enum values on the wire, and third-party vocabulary
(schema.org `Organization`, GitHub's `organization`) are contracts, and
anglicising them breaks them. `lib/colors.ts` is the module name; `colour` is
the word inside it.

Comments and docstrings follow the prose rule, not the code rule, except when
they name a symbol or quote a wire value.

## Do

- Ask `lib/colors.ts` for a meaning and `lib/typography.ts` for a role. Add
  new colour meaning to the registry, not to the call site.
- Use the tint triplet `border-{tone}/40 bg-{tone}/10 text-{tone}` for every
  status chip, and let `--success` / `--warning` / `--info` / `--destructive`
  supply their semantic colours.
- Pick one of the four title steps (`TITLE.hero` / `.page` / `.section` /
  `.card`) and let size carry the hierarchy.
- Keep the radius ramp at `rounded-xs` (1px) for sub-8px elements,
  `rounded-sm` (2px) for interactive, `rounded-md` (3px) for containers.
- Give every control in a row the same `size` prop so the 28/32/36px ramp
  keeps them aligned.
- Align mixed icon/text rows with matched line boxes and `items-start`.
- Run `bun run check:design`, `check:contrast` and `check:controls` before
  calling UI work finished.
- Write labels in sentence case with acronyms preserved.
- Write every user-facing string in British English: `optimise`, `analyse`,
  `colour`, `centre`, `catalogue`.
- Keep the Console dark-only. Do not add theme selectors, stored-theme
  resolution or operating-system theme listeners.

## Don't

- Write a hex, `hsl()`, `rgb()` or a raw Tailwind palette class
  (`bg-blue-500`, `text-zinc-400`) anywhere in app code.
- Use the brand copper outside `overmind-eye-copper.svg`. Orange in the UI
  means warning.
- Add a shadow outside confirmation dialogs. Use tone, border or ring for
  every other surface; the only elevation token is `shadow-confirmation`.
- Use `rounded-lg`, `rounded-xl`, `rounded-full`, bare `rounded`, or an
  arbitrary `rounded-[Npx]`.
- Pair a title class with a `text-*` size or a weight utility. Mondwest has one
  master, so a weight utility renders synthesized bold.
- Apply `uppercase`, `text-transform`, or `tracking-wide/wider/widest`.
- Use `text-[Npx]`; the tokenized ramp bottoms out at `text-xs` (12px).
- Put a label inside a ghost button. A labelled button takes a filled variant.
- Import from `lucide-react` or the raw glyph modules under `ui/icons/`; render
  a semantic name from the `Icon` registry so one concept maps to one glyph.
- Hand-roll a spinner (`animate-spin`), a `window.confirm()`, or a second
  score-tier map. Use `Spinner`, `ConfirmDialog`, and `scoreTone`.
- Add `dark:` variants. Components use the single semantic palette directly.
- Correct alignment with `mt-px` or `pt-0.5`. Fix the line boxes.
- Animate on hover with scale, lift or shadow growth.
- Anglicise a contract to match the prose. CSS properties, DOM and library
  APIs, API routes, query keys, OTel attributes and wire enum values keep their
  American spelling; only the words a user reads change.
