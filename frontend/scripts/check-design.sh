#!/usr/bin/env bash
# Class-string violations Biome can't lint. `bun run check:design`; exit 1 on
# any hit. `--counts` prints per-rule totals and always exits 0.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

SRC=src
INCLUDE=(--include=*.tsx --include=*.ts)
# Migration escape hatch; the sentinel matches nothing in the steady state.
ALLOW='XXX_NO_MATCH_SENTINEL'

fail=0
report() {
  local name="$1" pattern="$2" extra_exclude="${3:-$ALLOW}"
  local hits
  hits=$(grep -rnE "$pattern" "$SRC" "${INCLUDE[@]}" 2>/dev/null \
    | grep -vE "$extra_exclude" || true)
  local count
  count=$(printf '%s' "$hits" | grep -c . || true)
  if [[ "${MODE:-check}" == counts ]]; then
    printf '%5s  %s\n' "$count" "$name"
  elif [[ "$count" -gt 0 ]]; then
    echo "✗ $name ($count):"
    printf '%s\n' "$hits" | sed 's/^/    /'
    fail=1
  fi
}

[[ "${1:-}" == "--counts" ]] && MODE=counts

report "raw palette status colours" \
  '\b(bg|text|border|ring|fill|stroke|from|to|via|decoration|outline|divide|shadow|accent|caret|ring-offset)-(red|rose|green|emerald|blue|violet|teal|amber|orange|yellow|purple|indigo|sky|cyan|lime|pink|fuchsia)-[0-9]'
# Deliberate theme-independent surfaces are named in lib/colors.ts; components
# reference those rather than re-spelling them.
report "raw neutral palette (use lib/colors.ts)" \
  '\b(bg|text|border|hover:bg|hover:text|hover:border|dark:bg|dark:text|dark:border|dark:hover:bg)-(zinc|neutral|slate|gray|stone)-[0-9]' \
  'lib/colors.ts'
# lib/colors.ts is the one module allowed to name a colour.
report "colour literal in code (use lib/colors.ts)" \
  '"#[0-9a-fA-F]{3,8}"|hsl\([0-9]|rgb\([0-9]' \
  'lib/colors\.ts|\.test\.'
# scoreTone/domainStatus in lib/colors.ts are the only score-tier / status→tone
# maps; a second copy drifts.
report "re-derived score tier (use scoreTone)" \
  'pct >= 70|>= 70 \?|value >= 70' \
  'lib/colors\.ts|\.test\.'
report "hex in class strings" \
  '(bg|text|border|ring|fill|stroke|from|to|via)-\[#'
# The elevation wash is two steps in both themes. `bg-muted/N` alphas step
# 0.25–0.5 L* against a ~1 L* just-noticeable difference, so they render flat.
report "alpha wash (use bg-wash-subtle / bg-wash-raised)" \
  'bg-muted/[0-9]'
# The tokens are hex, not HSL triplets, so hsl() receives a colour and the
# browser silently discards the whole declaration, in both themes.
report "hsl() wrapping a hex token (drops the declaration)" \
  'hsl\(var\(--'
# An opaque bg-white/bg-black is the same fill in both themes, so one theme is
# always wrong; bg-foreground/bg-background invert on their own. The `dark:bg-`
# exemption covers NATIVE_DATE_TRIGGER, which forces light chrome in dark mode
# to match the native datetime picker's non-themeable popup.
report "opaque bg-white/bg-black (use bg-foreground / bg-background)" \
  '\bbg-(white|black)[^/a-zA-Z0-9-]' \
  'dark:bg-'
# Allowlisted: login, a bespoke display surface whose type sits off the UI
# data-scale.
report "arbitrary text-[Npx/rem/em] (use scale tokens)" \
  'text-\[[0-9.]+(px|rem|em)\]' \
  'routes/login.tsx'
# Three faces, three jobs: PP Mondwest = titles, Geist Pixel = everything else
# (the default, needs no class), NeueBit = sidebar nav and breadcrumb only. A
# title picks a step from lib/typography.ts rather than spelling the face.
report "font-display / page-title (use TITLE.* from lib/typography)" \
  '\bfont-display\b|"page-title|page-title ' \
  'lib/typography\.ts|styles\.css'
report "title class + explicit text size (size comes from the step)" \
  '(title-hero|page-title|title-section|title-card)[^"'"'"']*\btext-(xs|sm|base|lg|xl|2xl|3xl|4xl|5xl)\b|\btext-(xs|sm|base|lg|xl|2xl|3xl|4xl|5xl)\b[^"'"'"']*(title-hero|page-title|title-section|title-card)' \
  'lib/typography\.ts|styles\.css'
# A raw `prose-body` bypasses the registry: reading surfaces stop being greppable.
report "prose-body literal (use PROSE from lib/typography)" \
  'prose-body' \
  'lib/typography\.ts|styles\.css'
# Mondwest ships a Regular master only — a weight utility synthesizes bold.
report "title + weight utility (Mondwest has one master)" \
  '(title-hero|page-title|title-section|title-card)[^"'"'"']*font-(medium|semibold|bold)|font-(medium|semibold|bold)[^"'"'"']*(title-hero|page-title|title-section|title-card)' \
  'lib/typography\.ts|styles\.css'
# NeueBit's bold master is reserved for its accent surfaces (active sidebar row,
# breadcrumb leaf), never for titles.
report "font-display + weight (titles are single-weight)" \
  'font-display[^"]*font-(medium|semibold|bold)|font-(medium|semibold|bold)[^"]*font-display'
report "inline fontFamily (use font-* utilities)" \
  'fontFamily'
# Both faces were deleted from public/fonts, so either literal now falls through
# to the system stack instead of failing loudly.
report "retired font family literal (Montreal / NeueBit Text are gone)" \
  'PP Neue Montreal|NeueBit Text'
# Allowlisted: the sidebar nav rows (via LABEL.sidebar) and the breadcrumb —
# the only surfaces NeueBit is an accent for.
report "font-sidebar outside the nav surfaces (NeueBit is an accent)" \
  '\bfont-sidebar\b|--font-sidebar' \
  'app-sidebar\.tsx|routes/_auth\.tsx|lib/typography\.ts|styles\.css'
# Geist Pixel quantises every advance to a 38/1000em grid; `tracking-cell` is
# that step and arbitrary values land glyphs between cells.
report "arbitrary tracking (use tracking-cell, one Geist Pixel grid cell)" \
  'tracking-\[' \
  'routes/login\.tsx'
# Dead shadow utilities (all --shadow-* resolve to none).
report "shadow-* utilities (shadows are off)" \
  '\bshadow-(2xs|xs|sm|md|lg|xl|2xl)\b'
# The ramp is three steps and nothing above them:
#   rounded-xs (1px)  things under ~8px — status dots, progress bars, switch
#   rounded-sm (2px)  anything interactive — buttons, inputs, chips, badges
#   rounded-md (3px)  containers only — cards, dialogs, popovers, sheets
# `lg`/`xl`/`full` are deliberately not aliased down: an alias lets
# `rounded-full` keep appearing while quietly meaning 3px.
report "off-ramp radius (use rounded-xs/sm/md)" \
  '\brounded-(lg|xl|2xl|3xl|full)\b'
report "arbitrary radius (use rounded-xs/sm/md)" \
  'rounded(-[trbl]{1,2})?-\[[^]]+\]'
# Bare `rounded` resolves to Tailwind's own 0.25rem, not a styles.css token.
report "bare rounded (untokenised; use rounded-sm)" \
  '["'"'"'\`][^"'"'"'\`]*\brounded[^-a-zA-Z0-9_]'
# The brand copper lives in assets/overmind-eye-copper.svg and nowhere else.
# Orange in the UI means "warning" only, from --warning / the score-tier scale.
report "brand copper outside the logo asset" \
  '#(ed670f|ED670F|f3a56a|F3A56A|c8956a|C8956A|f69a5b|F69A5B|d4a54a|D4A54A)'
# Casing is authored via sentenceCase / humanizeKey in lib/label-case.ts. A CSS
# `uppercase` hides the authored string, so it drifts unseen.
report "uppercase / text-transform (labels are sentence case)" \
  '\buppercase\b|text-transform|textTransform' \
  'lib/label-case.ts'
# `.pixel-label` already carries the label letter-spacing.
report "tracking-wide/wider/widest (label spacing comes from .pixel-label)" \
  '\btracking-(wide|wider|widest)\b' \
  'ui/dropdown-menu.tsx'
report "text-black dark:text-white (use text-foreground)" \
  'text-black dark:text-white|dark:text-white .*text-black'
report "confirm() (use ConfirmDialog)" \
  '\bwindow\.confirm\(|[^.]\bconfirm\('
report "lucide-react import (use @/components/ui/icons)" \
  'from "lucide-react"'
# Allowlisted: ui/icons/index.ts, the one registry that imports glyphs directly.
report "raw pixelart glyph import (use @/components/ui/icons)" \
  'from "(\./|@/components/ui/icons/)(pixelart|page-title)"' \
  'ui/icons/index.ts'
# Allowlisted: the Spinner primitive itself and `spinning ?` refresh buttons
# whose onAnimationIteration handler resets state (Spinner can't forward it).
report "animate-spin (use Spinner)" \
  '\banimate-spin\b' \
  'ui/spinner.tsx|spinning \?'
# A route that early-returns an empty state instead of its PageShell renders a
# page with no title, and a required `PageShell.header` cannot catch it — there
# is no PageShell on that path. components/ never early-returns at route level.
report "empty state returned instead of PageShell (put it inside)" \
  'return <(ProjectRequired|NoProjects)EmptyState' \
  'components/'
report "blur / gradient (flat surfaces only)" \
  'backdrop-blur|\bblur-(sm|md|lg|xl|2xl|3xl)\b|bg-gradient-|bg-(linear|radial|conic)-'

if [[ "${MODE:-check}" == counts ]]; then
  exit 0
fi
if [[ "$fail" -eq 0 ]]; then
  echo "✓ design guardrail: no violations"
fi
exit "$fail"
