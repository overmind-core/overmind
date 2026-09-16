## What

<!-- The change, in a sentence or two. -->

## Why

<!-- The problem it solves. Link the issue if there is one. -->

## Verification

<!-- The commands you actually ran, with their outcome. Say plainly if you skipped
     the suite because the change was self-evident. -->

```
```

## Screenshots

<!-- UI changes only: before / after, in both light and dark. -->

## Completeness

<!-- CI cannot catch these. Tick what applied and say n/a for the rest — say which,
     don't leave a box blank. Details: the pr-etiquette skill. -->

- [ ] **MCP** — classify (MCP-ready / CLI-guided / frontend-only / out of scope); catalog, contracts, tools, prompts, resources, `tests/test_mcp_*.py`
- [ ] **Blast radius** — celery routing, `seed_demo`, generated OpenAPI client
- [ ] **Docs** — `overmind-core/docs` PR opened and linked, if user-visible behaviour changed
- [ ] **Agent config** — `AGENTS.md` or the affected skill updated, if this changes behaviour they describe

## Checklist

- [ ] The old path is deleted, not left standing beside the new one
- [ ] Tests cover the change, or the reason they don't is stated above
- [ ] Model changes: rebased on `main`, migration regenerated on top, `make check-migrations` passes
- [ ] UI changes pass `bun run check:all` and were checked in both themes
