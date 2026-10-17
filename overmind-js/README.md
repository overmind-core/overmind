# overmind (TypeScript)

Observability only. A thin layer over the Langfuse tracing SDK: Langfuse's `observe`, `startObservation`, `startActiveObservation`, `updateActiveObservation` and `propagateAttributes` are re-exported unchanged, and every span is rewritten into Overmind's attribute contract and sent as OTLP/protobuf to `{baseUrl}/api/v1/traces` with `X-Api-Key`. Private; not published.

```ts
import { init, observe, withCapability, shutdown } from "overmind";

init({ capabilityId: "…" }); // OVERMIND_API_KEY, OVERMIND_API_URL, OVERMIND_CAPABILITY_ID, OVERMIND_PROJECT_ID

const answer = observe(async (q: string) => "…", { name: "answer", asType: "generation" });
await withCapability({ id: "…", name: "Refunds" }, () => answer("hi"));
await shutdown();
```

`observe` is Langfuse's, plus the declarations `overmind chassis` and `overmind sync` read from source: `capabilityId` (binds the call and its descendants to a capability; set it on entry points), `capabilityName`, `intent`, `expectations` and `referenceTrajectory`.

`src/transform.ts` owns the mapping (observation type → `overmind.span.type`, usage/cost/model parameters → `genai.*` plus `gen_ai.usage.*`, input/output → `inputs`/`outputs`, session/trace name → `conversation.id`/`overmind.workflow.name`, metadata → `metadata.*`). Spans with no Langfuse, GenAI or Overmind attributes stay local.

```
bun run typecheck && bun run test && bun run build
```
