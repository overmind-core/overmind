# Instrumentation

Live scoring reads what the SDK decorators write on each span: the capability
identity, input and output, tool names, and optional `intent`,
`expectations` and `reference_trajectory`.

## Use the instrumentation plan

A JavaScript/TypeScript anchor has no placement: those applications export OTLP from their
existing OpenTelemetry setup, as [javascript.md](javascript.md#instrument-with-opentelemetry-not-an-sdk) describes.

Call `get_instrumentation_plan`, optionally with a capability. Each placement
names a `target` (`file`, `qualname`, `module`, `import_line`), its `kind`
(`entry_point`, `workflow`, `tool`, `retrieval` or `function`) and the exact
`required_decorator`. The entry point carries the capability id:

```python
@overmind.entry_point(capability_id="<capability id from the placement>")
def handle(message: str) -> str: ...
```

Apply each decorator verbatim. Declare the expectations a span's own contract
implies on its decorator; `/overmind setup` writes them from the capability
card and decorates every tool, retrieval step, mode and helper the card names,
not only the plan's anchors. Judge criteria live only on decorators.
Deterministic checks (required keys, field rules, budgets) live in the toml's
`eval_matrix`. After changing decorator expectations, run `overmind sync`: it
copies the expectations on entry points, workflows and plain `observe` spans
into the capability's Default eval set, which dataset runs, backtests, the
optimizer and training grade with. See [evals.md](evals.md#configure).

```python
@overmind.tool(expectations=["Returns the order for the given id"])
def lookup_order(order_id: str) -> dict: ...


@overmind.entry_point(
    capability_id="...",
    reference_trajectory=["lookup_order", "issue_refund"],
)
def refund(message: str) -> str: ...
```

`intent=` overrides the user request read from the root input; descendants
inherit it. `overmind.session(conversation_id)` groups spans into a session for
the block. `overmind.record_llm_call(model, total_tokens, cost)` stamps usage
for LLM clients the provider instrumentations do not cover.

If the plan has `human_action` or no placements, report its instruction and
stop this attempt. When the registry is unavailable, run local
`/overmind setup` followed by `overmind sync`, then request the plan again in a
new attempt. Do not guess anchors or decorator targets.

## Verify

The server cannot edit the target files. Apply the placements locally, run the
application, then pass the run's `trace_id` to `verify_instrumentation`. The
check is read-only. Per capability it reports `identity`, `root`, `input`,
`output`, `tools` and `scored_rows` as `ok` or `missing`, a punch list with the
fix for each gap, and the eval matrix rows that would score each span.
