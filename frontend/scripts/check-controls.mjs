#!/usr/bin/env node
/**
 * Rules about a JSX element's *contents* and its siblings, which the grep rules
 * in `check-design.sh` cannot see. `bun run check:controls`; exits 1 on any
 * violation.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (p.endsWith(".tsx")) out.push(p);
  }
  return out;
}

/** End index of a JSX open tag starting just past `<Name`, brace-aware. */
function tagEnd(s, i) {
  let depth = 0;
  let q = null;
  for (let j = i; j < s.length; j++) {
    const c = s[j];
    if (q) {
      if (c === q && s[j - 1] !== "\\") q = null;
    } else if (c === '"' || c === "'" || c === "`") q = c;
    else if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (depth === 0 && c === ">") return { end: j, selfClosing: s[j - 1] === "/" };
  }
  return null;
}

/** The tag's own `className`, ignoring any nested in a slot like `end={<Badge …/>}`. */
function ownClassName(attrs) {
  let depth = 0;
  let q = null;
  for (let j = 0; j < attrs.length; j++) {
    const c = attrs[j];
    if (q) {
      if (c === q && attrs[j - 1] !== "\\") q = null;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      q = c;
      continue;
    }
    if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (depth === 0 && attrs.startsWith("className=", j)) {
      const value = attrs.slice(j + "className=".length);
      const m = value.match(/^(?:"([^"]*)"|\{((?:[^{}]|\{[^{}]*\})*)\})/);
      return m ? (m[1] ?? m[2] ?? "") : "";
    }
  }
  return "";
}

/** Matching `</Button>` for a body starting at `i`, nesting-aware. */
function bodyEnd(s, i) {
  let depth = 1;
  for (let j = i; j < s.length; j++) {
    if (s.startsWith("<Button", j)) depth++;
    else if (s.startsWith("</Button>", j)) {
      depth--;
      if (depth === 0) return j;
    }
  }
  return -1;
}

function rootChildren(body) {
  let depth = 0;
  let roots = 0;
  let j = 0;
  while (j < body.length) {
    if (body[j] === "<" && body[j + 1] === "/") {
      depth--;
      j = body.indexOf(">", j) + 1;
      continue;
    }
    const m = body.slice(j).match(/^<[A-Za-z][\w.]*/);
    if (m) {
      if (depth === 0) roots++;
      const t = tagEnd(body, j + m[0].length);
      if (!t) break;
      if (!t.selfClosing) depth++;
      j = t.end + 1;
      continue;
    }
    j++;
  }
  return roots;
}

const BY_TAG = {
  Button: { default: 32, lg: 36, sm: 28, xs: 24 },
  Chip: { default: 28, lg: 32, sm: 24 },
  Input: { default: 32, lg: 36, sm: 28 },
  SelectTrigger: { default: 32, lg: 36, sm: 28 },
};

const HEIGHT = {
  "Button:default": 32,
  "Button:icon": 32,
  "Button:icon-lg": 36,
  "Button:icon-sm": 28,
  "Button:icon-xs": 24,
  "Button:lg": 36,
  "Button:sm": 28,
  "Button:xs": 24,
  "Input:default": 32,
  "Input:lg": 36,
  "Input:sm": 28,
  "SelectTrigger:default": 32,
  "SelectTrigger:lg": 36,
  "SelectTrigger:sm": 28,
};

const ACRONYMS =
  /^(API|PR|LLM|OK|JSON|CSV|JSONL|ID|IDs|GitHub|OpenAI|GPU|OTEL|SDK|CLI|UI|URL|LoRA|FT|N\/A)$/;

// Proper nouns exempt from sentence case (PRODUCT.md terminology). Matched as
// whole phrases, so "Data" clears only inside "Data Workshop". Add terms here
// rather than loosening the word-level check.
const PRODUCT_TERMS = [
  "Data Workshop",
  "Agent Testing",
  "Model Training",
  "Context Graph",
  "Overmind",
  "Optimiser",
  "Observability",
  "Console",
  "Datasets",
  "Training",
  "Inference",
  "Evaluations",
  "Evals",
  "Eval",
  "Experiments",
  "Pro",
  "Free",
  "Langfuse",
  "OTLP",
].map((term) => term.split(" "));

function productTermSpans(words) {
  const covered = new Set();
  for (const termWords of PRODUCT_TERMS) {
    for (let i = 0; i <= words.length - termWords.length; i++) {
      const matches = termWords.every((tw, j) => words[i + j].replace(/[^A-Za-z/]/g, "") === tw);
      if (matches) for (let j = 0; j < termWords.length; j++) covered.add(i + j);
    }
  }
  return covered;
}

const violations = [];

for (const file of walk(SRC)) {
  const s = readFileSync(file, "utf8");
  const lines = s.split("\n");
  const rel = file.slice(file.indexOf("/src/") + 1);
  const controls = [];

  for (const tag of ["Button", "SelectTrigger", "Input", "Chip"]) {
    let i = 0;
    while (true) {
      const k = s.indexOf(`<${tag}`, i);
      if (k < 0) break;
      const after = s[k + 1 + tag.length];
      if (!" \t\n/>".includes(after)) {
        i = k + 1 + tag.length;
        continue;
      }
      const t = tagEnd(s, k + 1 + tag.length);
      if (!t) break;
      const attrs = s.slice(k + 1 + tag.length, t.end);
      const line = s.slice(0, k).split("\n").length;
      i = t.end + 1;

      // `size-N` sets height too on a square icon button, so it counts as a
      // hand-patched height.
      const h = attrs.match(/\bclassName="[^"]*?\b(h-\d+(?:\.5)?|size-\d+(?:\.5)?)\b/);
      if (h) {
        violations.push(
          `${rel}:${line}  ${tag} patches ${h[1]} — use the shared size ramp instead`
        );
      }

      const sizeMatch = attrs.match(/\bsize="([^"]*)"/);
      const size = sizeMatch ? sizeMatch[1] : attrs.includes("size=") ? null : "default";
      const px = size ? HEIGHT[`${tag}:${size}`] : null;
      const indent = lines[line - 1].length - lines[line - 1].trimStart().length;
      if (px) controls.push({ indent, line, px });

      if (tag !== "Button" || t.selfClosing) continue;
      const be = bodyEnd(s, t.end + 1);
      if (be < 0) continue;
      const body = s.slice(t.end + 1, be);
      i = be + 9;

      const mr = body.match(/\bmr-\d+(?:\.5)?\b/);
      if (mr) {
        violations.push(
          `${rel}:${line}  icon uses ${mr[0]} — button icon spacing comes from the primitive's gap`
        );
      }

      const label = body
        .replace(/<span[^>]*\bsr-only\b[\s\S]*?<\/span>/g, " ")
        // Braces first (a Link's `search={(prev) => prev}` is not label text),
        // then any remaining markup.
        .replace(/\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}/g, " ")
        .replace(/<[^>]*>/g, " ")
        .replace(/\s+/g, " ")
        .trim();

      if (attrs.includes('variant="ghost"') && label) {
        violations.push(`${rel}:${line}  ghost button labelled "${label}" — ghost is icon-only`);
      }

      // Radix Slot throws at render if `asChild` gets more than one element
      // child — the icon goes inside the child, not beside it.
      if (attrs.includes("asChild") && rootChildren(body) > 1) {
        violations.push(
          `${rel}:${line}  asChild button has ${rootChildren(body)} element children — Slot needs one`
        );
      }

      const words = label.split(" ").filter(Boolean);
      const productTermIdx = productTermSpans(words);
      for (let idx = 1; idx < words.length; idx++) {
        if (productTermIdx.has(idx)) continue;
        const bare = words[idx].replace(/[^A-Za-z/]/g, "");
        if (bare && /^[A-Z]/.test(bare) && !ACRONYMS.test(bare)) {
          violations.push(`${rel}:${line}  label "${label}" is not sentence case ("${bare}")`);
          break;
        }
      }
    }
  }

  // A flex row capped below the control it holds pushes that control off the
  // centre line. Scoped by indentation so a sibling below the row is not read
  // as a child.
  for (let n = 0; n < lines.length; n++) {
    const box = lines[n].match(/(?<!min-)\bh-(\d+)\b/);
    if (!box || !lines[n].includes("flex")) continue;
    const px = Number(box[1]) * 4;
    const indent = lines[n].length - lines[n].trimStart().length;
    const subtree = [];
    for (let m = n + 1; m < lines.length; m++) {
      const l = lines[m];
      if (l.trim() === "") continue;
      if (l.length - l.trimStart().length <= indent) break;
      subtree.push(l);
    }
    const window = subtree.join("\n");
    for (const [tag, sizes] of Object.entries(BY_TAG)) {
      const m = new RegExp(`<${tag}\\b([^>]*)>`).exec(window);
      if (!m) continue;
      const sz = m[1].match(/\bsize="([^"]*)"/);
      const childPx = sizes[sz ? sz[1] : "default"];
      if (childPx && childPx > px) {
        violations.push(
          `${rel}:${n + 1}  flex row capped at ${px}px holds a ${childPx}px <${tag}> — drop the h-*`
        );
        break;
      }
    }
  }

  // The modal canvas belongs to the primitive: capped at 75vw × 75vh, width
  // picked with `size`.
  if (!rel.endsWith("ui/sidebar.tsx")) {
    for (const tag of ["DialogContent", "SheetContent", "AlertDialogContent"]) {
      let i = 0;
      while (true) {
        const k = s.indexOf(`<${tag}`, i);
        if (k < 0) break;
        const t = tagEnd(s, k + 1 + tag.length);
        if (!t) break;
        const attrs = s.slice(k + 1 + tag.length, t.end);
        i = t.end + 1;
        const bad = attrs.match(
          /\b(?:sm:)?(?:max-)?[wh]-\[[^\]]+\]|\b(?:sm:)?max-w-(?:xs|sm|md|lg|xl|\d?xl|none)\b|\bp-0\b/
        );
        if (bad) {
          const line = s.slice(0, k).split("\n").length;
          violations.push(
            `${rel}:${line}  ${tag} sets ${bad[0]} — the canvas comes from \`size\`, not the call site`
          );
        }
      }
    }
  }

  for (const tag of ["DialogHeader", "DialogFooter", "SheetHeader", "SheetFooter"]) {
    let i = 0;
    while (true) {
      const k = s.indexOf(`<${tag}`, i);
      if (k < 0) break;
      const t = tagEnd(s, k + 1 + tag.length);
      if (!t) break;
      const attrs = s.slice(k + 1 + tag.length, t.end);
      i = t.end + 1;
      const bad = ownClassName(attrs).match(/\bp[xy]?-\d+(?:\.\d+)?\b|\bborder-[bt]\b/);
      if (bad) {
        const line = s.slice(0, k).split("\n").length;
        violations.push(
          `${rel}:${line}  ${tag} re-adds ${bad[0]} — the slot supplies padding and dividers`
        );
      }
    }
  }

  controls.sort((a, b) => a.line - b.line);
  let group = [];
  const flush = () => {
    if (group.length > 1 && new Set(group.map((g) => g.px)).size > 1) {
      violations.push(
        `${rel}:${group[0].line}  row mixes control heights (${[...new Set(group.map((g) => `${g.px}px`))].join(", ")})`
      );
    }
  };
  for (const c of controls) {
    const prev = group[group.length - 1];
    if (prev && c.indent === prev.indent && c.line - prev.line <= 12) group.push(c);
    else {
      flush();
      group = [c];
    }
  }
  flush();
}

// Button, Input, SelectTrigger and Chip share one content box, or a label
// starts a pixel further in than its neighbour's: each carries a 1px border
// (transparent where no edge shows) and the same `px-*`.
{
  const RAMP = [
    ["components/ui/button.tsx", "px-3"],
    ["components/ui/input.tsx", "px-3"],
    ["components/ui/select.tsx", "px-3"],
    ["components/ui/chip.tsx", "px-3"],
  ];
  // Comments in these files talk ABOUT the contract; strip them so only the
  // class strings are asserted.
  const classSource = (rel) =>
    readFileSync(join(SRC, rel), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
  for (const [rel, pad] of RAMP) {
    const src = classSource(rel);
    if (!/\bborder\b/.test(src)) {
      violations.push(
        `${rel}  control ramp: no 1px border — an outlined sibling's content sits 1px in`
      );
    }
    if (!src.includes(pad)) {
      violations.push(`${rel}  control ramp: default horizontal padding is not ${pad}`);
    }
  }
  for (const rel of [
    "components/ui/button.tsx",
    "components/ui/input.tsx",
    "components/ui/select.tsx",
    "components/ui/chip.tsx",
    "components/ui/textarea.tsx",
    "components/ui/switch.tsx",
  ]) {
    const src = classSource(rel);
    const ring = src.match(/focus-visible:ring-\[[^\]]+\]/);
    if (ring) {
      violations.push(`${rel}  focus ring is ${ring[0]} — the ramp's ring is \`ring-2\``);
    }
  }
}

if (violations.length === 0) {
  console.log("✓ controls: ghost is icon-only, spacing and heights come from the primitives");
  process.exit(0);
}
for (const v of violations) console.log(`  ${v}`);
console.log(`\n✗ ${violations.length} control violation(s)`);
process.exit(1);
