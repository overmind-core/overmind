---
name: engineering-taste
description: The engineering bar for this repo — rebuild from first principles rather than patching, the simplicity ladder, what must never be simplified away, and the one-runnable-check rule. Use when designing a change, choosing between approaches, adding a dependency or abstraction, deciding whether to keep a legacy path, or reviewing for over-engineering.
---

# Engineering taste

Efficient, not careless. The best code is the code never written.

## Default to rebuilding, not patching

The platform has no real users yet. A request to change something is a request to
build it the right way from first principles — not to add a tweak beside the wrong
shape. Core code logic and tests owe nothing to backwards compatibility in almost
every case: delete the old path rather than keeping it alive behind a flag, a
shim, an `_v2` name, or an "if legacy" branch.

Two things do carry forward, because production and staging hold real state:

- **Migrations.** Schema changes ship as migrations that run cleanly against the
  deployed databases.
- **Anything explicitly called out** as needing a transition.

The codebase is already bloaty. A change that leaves both the old and the new way
standing has made it worse, however small the diff looked. Removing the old path
is part of the change, not a follow-up.

## The ladder

Before writing any code, stop at the first rung that holds:

1. Does this need to be built at all?
1. Does the standard library already do it? Use it.
1. Does a native platform feature cover it? Use it.
1. Does an already-installed dependency solve it? Use it.
1. Can it be one line? Make it one line.
1. Only then: write the minimum code that works.

## Rules

- No abstractions that weren't explicitly requested.
- No new dependency if it can be avoided.
- No boilerplate nobody asked for.
- Deletion over addition. Boring over clever. Fewest files possible.
- Question complex requests: "Do you actually need X, or does Y cover it?"
- When two approaches are the same size, take the edge-case-correct one. Less code, not the flimsier algorithm.

## Blast radius is part of the work

A change is finished when every surface that reflects it has been updated, not when
the vertical it started in compiles. Before calling anything done, check what else
reads the thing you changed — the pr-etiquette skill lists the specific surfaces
that get missed here.

## Never simplify away

Input validation at trust boundaries. Error handling that prevents data loss. Security. Accessibility. The calibration real hardware needs — the platform is never the spec ideal, a clock drifts, a sensor reads off. Anything explicitly requested.

## Verify behavior

Follow the testing policy in AGENTS.md. Non-trivial logic needs runnable behavior verification; use the existing E2E flow and record its artifact. Do not create a per-function test or self-check merely because code was added.

## Naming a ceiling

A deliberate shortcut with a known limit — a global lock, an O(n²) scan, a naive heuristic, a fixed page cap — is worth one comment, and only if it names both the ceiling and the upgrade path. A shortcut with no ceiling to name needs no comment at all. See the code-comments skill.
