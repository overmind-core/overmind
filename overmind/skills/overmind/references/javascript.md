# JavaScript and TypeScript repositories

Read this file when the repository to onboard is JavaScript or TypeScript. The
workflow in [setup.md](setup.md) is the same: `overmind chassis`, the capability
snapshot, conversion to `overmind.toml`, `overmind sync`, instrumentation, a second
sync. This file lists where it differs. Where it is silent, follow setup.md.
The overmind CLI and converter are Python; only the application code is not.

## Detect the language

JavaScript/TypeScript when the repo root holds a `package.json` and most source
files outside `node_modules`, `dist`, `build` and `.next` are `.ts`, `.tsx`, `.mts`,
`.cts`, `.js`, `.jsx`, `.mjs` or `.cjs`. `.d.ts` files, minified bundles
(`*.min.js`, `*.bundle.js`) and files over 2 MB are not scanned.

`overmind chassis` scans one language per run and defaults to the repository's
majority language. Pass `--language typescript` (JavaScript included) or
`--language python` explicitly, and set the snapshot's top-level `"language"` to
the same value. When the other language also holds AI capabilities, name them in
the final summary as not scanned; do not mix qualnames of two languages in one snapshot.

Detect the package manager from the lockfile before running anything: `bun.lock` /
`bun.lockb` → bun, `pnpm-lock.yaml` → pnpm, `yarn.lock` → yarn, `package-lock.json`
→ npm. Never switch managers, and never add Python files or dependencies to the
repository.

## Tooling runs in an isolated Python environment

A JavaScript project has no Python environment, so the isolated runner in
[onboard.md](onboard.md#isolated-tooling-recovery) is the default here, not a
recovery. Use it for every CLI command (`init`, `chassis`, conversion, `skills sync`,
`sync`) and for both runtime checks:

```bash
uv tool run --isolated --python 3.11 --from "overmind==<version from the paste>" python -m overmind chassis --language typescript
```

Replace `python` in onboard.md's checks with that runner prefix; skip the part
that expects the interpreter or package path to belong to the project. Add
`python -m overmind chassis --help` to the checks: it must list `--language`, else
the SDK predates the TypeScript scanner and the scan is blocked until it is
upgraded. Keep the repository root as the working directory. `overmind.toml` and
`.overmind/` are language-neutral and unchanged. Without uv, create a dedicated
Python 3.10+ tooling virtual environment as onboard.md says.

## Scan differences

The chassis digest is ground truth, as in Python. What changes:

- **Qualnames** are `<module>.<symbol>`: the file path relative to the repo root,
  extension removed, `/` becomes `.` (`src/agent/run.ts` → `src.agent.run`; `index.ts`
  keeps `index`). Copy every `anchors` qualname verbatim from the digest.

  | Source                                                                             | Qualname                       |
  | ---------------------------------------------------------------------------------- | ------------------------------ |
  | `function f() {}`, `const f = () => {}`, `const f = withRetry(() => {...}, {...})` | `<module>.f`                   |
  | `class C { m() {} }`, `class C { m = () => {} }`                                   | `<module>.C.m`                 |
  | `const o = { m() {}, n: () => {} }`                                                | `<module>.o.m`, `<module>.o.n` |
  | function inside function `f`                                                       | `<module>.f.<locals>.inner`    |
  | `export default () => {}` (unnamed)                                                | `<module>.default`             |

  A class with a constructor is callable under its own name.

- **Source references** are `relative/path.ts#Lstart-Lend`; the schema's `.py` examples
  stand for the real extension. `file` on an anchor is the definition span.

- **Call graph** follows calls by name, including constructors, JSX components, and
  identifiers passed as arguments or object values (handlers, tool registries,
  `.map(fn)`). It is not type-aware: a dynamic `handlers[name]()` has no edge. Cite
  such a gap in `unresolved`; never edit a trajectory to force `verified`.

- **Prompts.** `system_prompt_span` may point at a string, a template literal (`${x}` is
  read as `{x}`), a `+` concatenation of literals, or a `dedent`/`outdent`/`.trim()`
  form, and at a `.md`/`.txt` prompt file. A span over a function or class is
  discarded. `bindings` is for Python `str.format` only: never supply it. A prompt
  built by a function or filled from runtime values is reconstructed into
  `system_prompt` with `{placeholders}`, plus an `unresolved` explanation.

- **Model resolution:** follow `process.env.X ?? "default"`, config objects, constructor
  options and SDK call sites (`openai`, `@anthropic-ai/sdk`, the Vercel AI SDK's
  `generateText({ model })`, LangChain.js, Mastra) through to the model id.

- **Product entries:** HTTP handlers (Next.js `app/**/route.ts` exports, Express/Hono/
  Fastify routes), `package.json` `bin` and `scripts` targets, queue consumers, and the
  agent object a user starts. Server actions and framework glue are not capabilities.

- **Tool inventory:** every entry in the registry (`tools: [...]`, `tool({ execute })`,
  `server.tool(...)`, a `switch` on the tool-call name). The dispatcher is an anchor,
  not a tool.

## Instrument with OpenTelemetry, not an SDK

Overmind has no JavaScript or TypeScript SDK and no decorators. A JavaScript
application sends traces to the OTLP endpoint with OpenTelemetry. The instrumentation
plan returns no placements for these anchors; `get_instrumentation_plan` answers with an
`instruction` pointing here. Do not install an Overmind package.

1. **Find the existing OpenTelemetry setup.** Look for `@opentelemetry/sdk-node`,
   `@opentelemetry/sdk-trace-node`, `@opentelemetry/exporter-trace-otlp-*`, `@vercel/otel`,
   an `instrumentation.ts` / `instrumentation.js` file, an OpenLLMetry (`@traceloop/*`) or
   OpenInference (`@arizeai/openinference-*`) instrumentation, or `OTEL_*` variables in the
   deployment config. With none, leave instrumentation pending and say so in the summary;
   scanning and sync are still complete.

1. **Add Overmind as one more OTLP exporter on that provider.** Keep the existing
   exporters, processors and instrumentations. Use the package already in the project
   (`@opentelemetry/exporter-trace-otlp-proto` is the protobuf exporter; add it only when
   the project has no OTLP/HTTP protobuf exporter). The endpoint takes OTLP/HTTP
   **protobuf**; JSON and gRPC are rejected.

   ```ts
   import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
   import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";

   const overmind = new BatchSpanProcessor(
     new OTLPTraceExporter({
       url: `${process.env.OVERMIND_API_URL ?? "https://api.overmindlab.ai"}/api/v1/traces`,
       headers: { "x-api-key": process.env.OVERMIND_API_KEY ?? "" },
     }),
   );
   // Add `overmind` to the existing spanProcessors list; never replace it.
   ```

   A project that configures export only through `OTEL_EXPORTER_OTLP_*` already points
   those variables at another backend: add the processor above in code instead of
   repointing them. Never write the key into source or a committed file; read
   `OVERMIND_API_KEY` from the environment and skip the processor when it is unset.

1. **Stamp the capability.** Set `overmind.capability.id` on the provider's resource to the
   id or slug synced into `overmind.toml` (`resourceFromAttributes({ "overmind.capability.id": "<id>" })`
   merged into the existing resource). With several capabilities in one process, stamp
   `overmind.capability.id` as a span attribute on each capability's root span instead.

1. **Mark the run.** Scoring needs a unit and a terminal: the root span of one execution
   carries `overmind.span.type = "entry_point"` and `overmind.unit_kind = "run"`, and the
   span holding the answer `overmind.delivery = "true"`. Set them where the application
   already starts that span; add a span only when none exists. The attributes Overmind
   reads, including the OTel GenAI and OpenLLMetry conventions that provider
   instrumentations already emit, are in the attribute table on the Overmind docs'
   observability page.

1. **Model calls and tools** need nothing extra when a provider instrumentation emits
   them. Do not wrap provider clients.

Rules that keep the chassis, the sync and live scoring consistent:

- **Change no behaviour.** The edit is limited to the telemetry bootstrap file, the run's
  root span attributes and the dependency manifest, with the repository's own package
  manager.
- **Expectations have no code home.** There are no decorators to carry them, so declare
  none; the seeded checks and the toml's `eval_matrix` entries still apply. Say so in the
  summary.
- **Short-lived scripts and serverless handlers** call `forceFlush()` or `shutdown()` on the
  provider before exiting so the batch processor sends the spans.
- **Sessions.** Put `conversation.id` on every span of a multi-turn exchange to group them.

## Verification

Same approval gate and server-side check as [telemetry.md](telemetry.md). Differences:
stamp the correlation as the `conversation.id` span attribute on the run's spans, run
through the project's own runtime and package manager, and flush or shut down the
provider before the process exits so the spans are sent. A typecheck (`tsc --noEmit`, or
the repository's own script) is the local check to report beside the changed files.

## Progress wording

The stage "Instrument capabilities" is complete when the Overmind exporter and capability
resource are in place and the run boundary is marked. With no existing OpenTelemetry it is
reported as pending, not as complete. Name the changed files as setup.md does.
