#!/usr/bin/env node
/**
 * Checks the `:root` (light) and `.dark` token blocks of src/styles.css in both
 * themes. `bun run check:contrast`; `--all` also prints passing rows. Exits 1 on
 * any failure.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const css = readFileSync(join(root, "src/styles.css"), "utf8");

// styles.css declares :root/.dark more than once (app palette, workshop
// instruments), so every block for a selector merges into one theme.
function block(selector) {
  const out = {};
  for (const m of css.matchAll(new RegExp(`${selector}\\s*\\{([^}]*)\\}`, "g"))) {
    for (const [, name, value] of m[1].matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
      out[name] = value;
    }
  }
  // --cat-N (via @theme inline) and --chart-N both alias --instrument-cat-N.
  for (let n = 1; n <= 6; n++) out[`cat-${n}`] = out[`instrument-cat-${n}`];
  for (let n = 1; n <= 5; n++) out[`chart-${n}`] = out[`instrument-cat-${n}`];
  return out;
}

const THEMES = { dark: block("\\.dark"), light: block(":root") };

const srgb = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
function luminance(hex) {
  let h = hex.replace("#", "");
  if (h.length === 3) h = [...h].map((c) => c + c).join("");
  const [r, g, b] = [0, 2, 4].map((i) => srgb(Number.parseInt(h.slice(i, i + 2), 16) / 255));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function ratio(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

// WCAG 2.1 AA: 4.5 for text, 3.0 for non-text UI (1.4.11) — control boundaries
// and marks that carry meaning alone. Dividers and card outlines are held to a
// 1.2 visibility floor instead: they do not identify a component, and 3.0 would
// replace this product's flat, shadowless border language with heavy boxes.
const TEXT = 4.5;
const UI = 3.0;
const DECOR = 1.2;
const PAIRS = [
  ["foreground", "background", TEXT, "body text on canvas"],
  ["foreground", "card", TEXT, "body text on card"],
  ["foreground", "popover", TEXT, "body text on popover"],
  ["muted-foreground", "background", TEXT, "secondary text on canvas"],
  ["muted-foreground", "card", TEXT, "secondary text on card"],
  ["muted-foreground", "muted", TEXT, "secondary text on muted fill"],
  ["card-foreground", "card", TEXT, "card text"],
  ["popover-foreground", "popover", TEXT, "popover text"],
  ["secondary-foreground", "secondary", TEXT, "secondary button text"],
  ["accent-foreground", "accent", TEXT, "text on hover wash"],
  ["primary-foreground", "primary", TEXT, "primary button label"],
  ["destructive-foreground", "destructive", TEXT, "destructive button label"],
  ["primary", "background", UI, "primary fill vs canvas"],
  ["border", "background", DECOR, "divider vs canvas"],
  ["border", "card", DECOR, "card outline vs card"],
  ["input", "card", UI, "INPUT outline vs card (a control boundary)"],
  ["ring", "background", UI, "focus ring vs canvas"],
  // Status tokens render as text at full strength over a 10% tint of
  // themselves, so text-vs-surface is the real check.
  ["success", "card", TEXT, "success text on card"],
  ["warning", "card", TEXT, "warning text on card"],
  ["info", "card", TEXT, "info text on card"],
  ["destructive", "card", TEXT, "error text on card"],
  ["success", "background", TEXT, "success text on canvas"],
  ["warning", "background", TEXT, "warning text on canvas"],
  ["info", "background", TEXT, "info text on canvas"],
  ["destructive", "background", TEXT, "error text on canvas"],
  // --cat-N is also chip text (text-cat-N), so it is held to the text bar.
  ...[1, 2, 3, 4, 5, 6].map((n) => [`cat-${n}`, "card", TEXT, `cat-${n} as chip text`]),
  ...[1, 2, 3, 4, 5].map((n) => [`chart-${n}`, "card", UI, `chart-${n} mark on card`]),
  ["sidebar-foreground", "sidebar", TEXT, "sidebar label"],
  ["sidebar-primary", "sidebar", UI, "sidebar active accent"],
  ["auth-text", "auth-panel", TEXT, "auth body text"],
  ["auth-text-label", "auth-panel", TEXT, "auth field label"],
  ["auth-accent", "auth-panel", UI, "auth accent"],
  ["auth-border", "auth-panel", DECOR, "auth divider"],
];

const showAll = process.argv.includes("--all");
let failed = 0;
const rows = [];

// `--border` has almost no headroom (1.32:1 on a card), so a level a step too
// low disappears. Each opacity is composited over its surface, floor DECOR.
const BORDER_LEVELS = [
  [1, "outline"],
  [0.7, "divider"],
  [0.6, "faint rule"],
];
const BORDER_SURFACES = ["background", "card", "muted", "popover"];

// Contrast ratio compresses for two adjacent surfaces (--card on --background
// is 1.08:1), so tiers are measured in ΔL*. The floor is 1.2 because large flat
// areas resolve at roughly 1 L*.
const TIER_MIN = 1.2;
const TIERS = [
  ["card", "background", "card vs canvas"],
  ["popover", "card", "popover vs card"],
  ["wash-subtle", "card", "subtle wash vs card"],
  ["wash-raised", "card", "raised wash vs card"],
  ["wash-raised", "wash-subtle", "the two wash steps"],
  ["control", "card", "control face vs card"],
  ["control-hover", "control", "control hover step"],
  ["accent", "background", "hover wash vs canvas"],
  ["muted", "card", "muted fill vs card"],
];

// Every neutral in BOTH themes rides one hue under a chroma ceiling. Hue is
// asserted only above HUE_MIN_CHROMA; below it a hue is quantisation noise.
const CHROMA_MAX = 6;
const HUE_AXIS = 75;
const HUE_TOLERANCE = 12;
const HUE_MIN_CHROMA = 1.3;
const NEUTRALS = [
  "background",
  "sidebar",
  "card",
  "popover",
  "muted",
  "secondary",
  "accent",
  "control",
  "control-hover",
  "border",
  "wash-subtle",
  "wash-raised",
];

/** CIELAB L*, C*, h° for a hex, D65. */
function lab(hex) {
  let h = hex.replace("#", "");
  if (h.length === 3) h = [...h].map((c) => c + c).join("");
  const [r, g, b] = [0, 2, 4].map((i) => srgb(Number.parseInt(h.slice(i, i + 2), 16) / 255));
  const X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047;
  const Y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
  const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const [fx, fy, fz] = [f(X), f(Y), f(Z)];
  const A = 500 * (fx - fy);
  const B = 200 * (fy - fz);
  return {
    C: Math.sqrt(A * A + B * B),
    h: ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360,
    L: 116 * fy - 16,
  };
}

/** `color-mix(in oklab, var(--border) N%, transparent)` over an opaque backdrop
 *  resolves, for contrast purposes, to the simple alpha composite. */
function channels(hex) {
  let h = hex.replace("#", "");
  if (h.length === 3) h = [...h].map((c) => c + c).join("");
  return [0, 2, 4].map((i) => Number.parseInt(h.slice(i, i + 2), 16));
}
function composite(fg, bg, alpha) {
  const f = channels(fg);
  const b = channels(bg);
  return `#${f
    .map((v, i) =>
      Math.round(alpha * v + (1 - alpha) * b[i])
        .toString(16)
        .padStart(2, "0")
    )
    .join("")}`;
}

for (const [theme, tokens] of Object.entries(THEMES)) {
  for (const [fg, bg, min, label] of PAIRS) {
    const a = tokens[fg];
    const b = tokens[bg];
    if (!a || !b) {
      rows.push([theme, label, fg, bg, null, min, "MISSING"]);
      continue;
    }
    const r = ratio(a, b);
    const ok = r >= min;
    if (!ok) failed++;
    if (!ok || showAll)
      rows.push([theme, label, `${fg} ${a}`, `${bg} ${b}`, r, min, ok ? "ok" : "FAIL"]);
  }
}

for (const [theme, tokens] of Object.entries(THEMES)) {
  const border = tokens.border;
  if (!border) continue;
  for (const surf of BORDER_SURFACES) {
    const bg = tokens[surf];
    if (!bg) continue;
    for (const [alpha, name] of BORDER_LEVELS) {
      const mixed = composite(border, bg, alpha);
      const r = ratio(mixed, bg);
      const ok = r >= DECOR;
      if (!ok) failed++;
      if (!ok || showAll)
        rows.push([
          theme,
          `border ${name} on ${surf}`,
          `${border} @${Math.round(alpha * 100)}%`,
          `${surf} ${bg}`,
          r,
          DECOR,
          ok ? "ok" : "FAIL",
        ]);
    }
  }
}

for (const [theme, tokens] of Object.entries(THEMES)) {
  for (const [a, b, label] of TIERS) {
    const x = tokens[a];
    const y = tokens[b];
    if (!x || !y) {
      rows.push([theme, `tier: ${label}`, a, b, null, TIER_MIN, "MISSING"]);
      failed++;
      continue;
    }
    const d = Math.abs(lab(x).L - lab(y).L);
    const ok = d >= TIER_MIN;
    if (!ok) failed++;
    if (!ok || showAll)
      rows.push([
        theme,
        `tier: ${label}`,
        `${a} ${x}`,
        `${b} ${y}`,
        d,
        TIER_MIN,
        ok ? "ok" : "FLAT",
      ]);
  }
}

// The sidebar is the one tier a theme may separate EITHER way — a tonal step or
// a visible --sidebar-border. Only neither fails.
for (const [theme, tokens] of Object.entries(THEMES)) {
  const step =
    tokens.sidebar && tokens.background
      ? Math.abs(lab(tokens.sidebar).L - lab(tokens.background).L)
      : 0;
  const edge = tokens["sidebar-border"];
  const hasEdge = Boolean(edge) && ratio(edge, tokens.sidebar ?? "#000000") >= DECOR;
  const ok = step >= TIER_MIN || hasEdge;
  if (!ok) failed++;
  if (!ok || showAll)
    rows.push([
      theme,
      "tier: sidebar wall",
      `sidebar ${tokens.sidebar ?? "—"}`,
      hasEdge ? `edge ${edge}` : "no edge",
      step,
      TIER_MIN,
      ok ? "ok" : "FLAT",
    ]);
}

for (const [theme, tokens] of Object.entries(THEMES))
  for (const name of NEUTRALS) {
    const hex = tokens[name];
    if (!hex) {
      rows.push([theme, `axis: ${name}`, name, "—", null, CHROMA_MAX, "MISSING"]);
      failed++;
      continue;
    }
    const { C, h } = lab(hex);
    if (C > CHROMA_MAX) {
      failed++;
      rows.push([
        theme,
        `axis: ${name} chroma`,
        `${name} ${hex}`,
        `C=${C.toFixed(1)}`,
        C,
        CHROMA_MAX,
        "SATURATED",
      ]);
    } else if (showAll) {
      rows.push([
        theme,
        `axis: ${name} chroma`,
        `${name} ${hex}`,
        `C=${C.toFixed(1)}`,
        C,
        CHROMA_MAX,
        "ok",
      ]);
    }
    if (C < HUE_MIN_CHROMA) continue;
    const drift = Math.abs(((h - HUE_AXIS + 540) % 360) - 180);
    if (drift > HUE_TOLERANCE) {
      failed++;
      rows.push([
        theme,
        `axis: ${name} hue`,
        `${name} ${hex}`,
        `h=${h.toFixed(0)}° want ${HUE_AXIS}°`,
        drift,
        HUE_TOLERANCE,
        "OFF-AXIS",
      ]);
    } else if (showAll) {
      rows.push([
        theme,
        `axis: ${name} hue`,
        `${name} ${hex}`,
        `h=${h.toFixed(0)}°`,
        drift,
        HUE_TOLERANCE,
        "ok",
      ]);
    }
  }

if (rows.length === 0) {
  console.log(
    "✓ contrast: every checked pairing clears AA in both themes, border ramp included;\n" +
      "  every surface tier is perceptibly separated and both ramps hold one hue axis"
  );
} else {
  const w = (s, n) => String(s).padEnd(n);
  console.log(
    `${w("theme", 6)} ${w("pairing", 30)} ${w("foreground", 26)} ${w("background", 26)} ${w("ratio", 7)} ${w("min", 5)} status`
  );
  for (const [theme, label, f, b, r, min, status] of rows) {
    console.log(
      `${w(theme, 6)} ${w(label, 30)} ${w(f, 26)} ${w(b, 26)} ${w(r ? r.toFixed(2) : "—", 7)} ${w(min, 5)} ${status}`
    );
  }
}
if (failed > 0) console.log(`\n✗ ${failed} pairing(s) below AA`);
process.exit(failed > 0 ? 1 : 0);
