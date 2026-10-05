---
name: code-comments
description: Full comment and docstring policy for this repo — what to delete, what to keep compressed, what to never touch, and the two local traps. Use when writing, reviewing, or pruning comments and docstrings in any language.
---

# Comments

A comment earns its place only by carrying information the code cannot. Apply this
test to every comment you write or read:

> If I delete this, does a competent engineer lose something they cannot readily
> infer from the code?

If no, delete it. Prefer fixing the name or the structure over explaining it.

## Delete

- Restatements of the next line or block.
- The purpose of a clearly named variable, function, component, prop or state.
- Straightforward control flow ("loop over rows", "bail out if empty").
- Section banners: `// ── Render ──`, `# ---- helpers ----`, `{/* Actions */}`,
  `# 1. fetch / # 2. transform`.
- Anything a type, name or signature already says.
- Standard framework behaviour: what `useEffect` does, what a Radix primitive
  renders, how React Query caches, what a DRF ViewSet is, what `@shared_task` does.
- History: what a change fixed, what used to be there, how many call sites there
  once were, PR numbers, incident dates. Gravestones for deleted code go with it.
- Plan-phase labels — `P0`, `Phase N`, `WS-H`, `Fork 3`, `Gap 2` — in code,
  comments, test names and filenames alike.
- JSDoc or a docstring that only re-spells the export name.

## Keep, compressed to one or two lines

- A non-obvious design decision, and why.
- A workaround for a framework, browser, API or third-party limitation.
- An edge case, anomaly, or intentionally counterintuitive behaviour.
- A performance, security, contrast, compatibility or correctness constraint.
- An external invariant: a backend contract, a wire format, a required ordering, a
  provider limit, a token the design system pins.
- A trap where the obvious simplification breaks something.
- Why an empty `catch {}` / `except: pass` / no-op guard is deliberate.

Write WHY, not WHAT. Never grow a short useful comment into prose.

## Large blocks

Multi-paragraph module headers and JSDoc essays are the main offender. Do not tidy
them — delete them, and keep only the constraint they contained. A module named
`colors.ts` exporting `TONE_CHIP` does not need six paragraphs saying it is about
colour. Turning a 20-line essay into a 15-line essay is a failure: it collapses to a
line or two, or it goes.

## Python specifics

A docstring is not automatically safe — judge it like a comment. Delete one that only
re-spells the name (`"""Serializer for EvalRun."""`), and delete Args/Returns blocks
that restate typed parameters. Keep a parameter line only for a unit, a range, an
ownership rule or a caller obligation the type cannot express.

Keep, compressed: idempotency and retry expectations on a Celery task, which queue a
task must run on, transaction and locking order, and why a write uses
`.filter().update()` — that pattern is deliberate here, it skips signals and leaves
the in-memory object stale.

## Never remove

`biome-ignore`, `@ts-expect-error`, `# noqa`, `# type: ignore`, `# pragma: no cover`,
`# fmt: off`, `eslint-*`, `@vitest-environment`, `"use client"`, shebangs, license
headers and generated-file banners. Django `verbose_name` / `help_text` are UI
strings, not comments.

Evaluate each TODO/FIXME on its merits. Keep one that names real outstanding work.

Prompt text inside a string literal is DATA, not commentary. Never edit inside a
string, f-string or triple-quoted block that is assigned, returned or passed as an
argument, however much it reads like prose.

## Two local traps

- `frontend/scripts/check-design.sh` greps source TEXT, comments included. A comment
  containing a banned literal (`uppercase`, `page-title`) fails the build. Reword it.
- A DRF view docstring becomes the OpenAPI operation `description`. Deleting one
  changes the generated client, so prefer an explicit `@extend_schema(summary=...)`.

## End state

Very few comments. A well-written file may legitimately have none. Do not keep a
comment because having some feels safer, and do not strip a load-bearing constraint
to lower the count.
