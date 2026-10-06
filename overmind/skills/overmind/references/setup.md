# Local repository setup

Declare the agent graph **in code with decorators**. Do not write capability
tables, cards, or eval matrices into `overmind.toml`.

MCP cannot scan or edit a repository. This workflow is local.

**Prerequisite:** project connection in [onboard.md](onboard.md) finished
(`overmind init` then a connection `overmind sync`, coding-agent reload).

## What changed

| Old (do not do)                                             | New                                                      |
| ----------------------------------------------------------- | -------------------------------------------------------- |
| Author `[capabilities.*]` in `overmind.toml`                | Decorate code with `@capability` / `@observe` / `task()` |
| Run `overmind chassis` / write `overmind_capabilities.json` | Run `overmind sync` — it AST-scans decorator call sites  |
| Hand-author `capability_card` / `eval_matrix` locally       | Server derives the card and Default eval set after sync  |

`overmind.toml` holds connection settings only: `base-url`, `project-id`,
`project-name`. Credentials stay in `.overmind/credentials.toml`.

## Workflow

1. Find the product AI entry points (CLI commands, HTTP handlers, top-level
   agent classes, orchestrators).
1. Decorate them in the repository — **this is the declaration**:

```python
from overmind import capability, observe, task, Expectation


@capability("triage", description="Resolve a support ticket end to end")
def handle_ticket(ticket: Ticket) -> Reply:
    with task("refund-flow", unit="turn"):
        order = lookup_order(ticket.order_id)
        return decide(ticket, order)


@observe(type="tool")
def lookup_order(order_id: str) -> Order: ...


@observe(
    type="llm",
    prompt=TRIAGE_PROMPT,
    expectations=[Expectation("constraint", "cites the refund policy")],
)
def decide(ticket: Ticket, order: Order) -> Reply: ...
```

3. Leave `overmind.toml` alone except for connection fields. Do not add
   capability sections.
1. Run **`overmind sync`**. It:
   - AST-scans Python sources for decorator / `with task(...)` call sites
     (never imports the app)
   - builds an AgentManifest (symbols, signatures, literal prompts,
     expectations, call-graph edges, repository fingerprint)
   - POSTs the manifest to `/api/v1/sync`
   - the server mints/updates capabilities, derives the card, creates
     behaviours from `task()` + the call graph, stores invokes edges, and
     enqueues Default-set preload
1. Re-read `overmind://capabilities/{capability}` and
   `get_instrumentation_plan`. Report unresolved kwargs sync printed, if any.

## Rules

- Slugs in `@capability("...")` are lowercase kebab-case. Never put UUIDs in
  code.
- Do not invent project or capability UUIDs.
- Do not hand-author capability cards, `overmind_capabilities.json`, or
  `[capabilities.*]` toml tables.
- Non-literal decorator kwargs are skipped and reported by sync; keep
  `description`, `prompt`, and `expectations` as string literals or
  module-level constants.
- Prefer `@capability` on the product entry, `@observe(type="tool"|"llm"|…)`
  on nested work, and `with task(key, unit="turn")` for scored units.
- After sync, the server owns the card and Default eval set. Read
  `eval_preload` on the capability resource; do not write `eval_matrix`
  locally.

Never upload the repository archive. Use MCP only with the intended project's
authentication.
