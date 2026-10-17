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

  | Source                                                                           | Qualname                       |
  | -------------------------------------------------------------------------------- | ------------------------------ |
  | `function f() {}`, `const f = () => {}`, `const f = observe(() => {...}, {...})` | `<module>.f`                   |
  | `class C { m() {} }`, `class C { m = () => {} }`                                 | `<module>.C.m`                 |
  | `const o = { m() {}, n: () => {} }`                                              | `<module>.o.m`, `<module>.o.n` |
  | function inside function `f`                                                     | `<module>.f.<locals>.inner`    |
  | `export default () => {}` (unnamed)                                              | `<module>.default`             |

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

## Instrument with `observe`, not decorators

The `overmind` npm package has no decorators. It re-exports Langfuse's tracing API and
rewrites each span into Overmind's contract. It is private and not on npm: install it
from the source the user or the paste names (the built `overmind-js` directory), with
the repository's own package manager (`bun add <path>`, `pnpm add <path>`,
`yarn add <path>`, `npm install <path>`). With no named source, leave instrumentation
pending and say why; scanning and sync are still complete. Confirm the import with the
project's runtime, for example `bun -e 'import { observe } from "overmind"; console.log(typeof observe)'`.

Every `observe`, `@overmind.*` and "decorator" step in setup.md maps as follows.
For a `.ts`/`.js` target, `get_instrumentation_plan` returns the same wrapper in
`required_decorator` (`<function>` stands for the original function literal) and
`import { observe } from "overmind";` in `target.import_line`. Apply it as the rows below
show, and add the `intent`, `expectations` and `referenceTrajectory` the card implies.

| Plan `kind`                        | Wrapper                                                                                  |
| ---------------------------------- | ---------------------------------------------------------------------------------------- |
| `entry_point`                      | `observe(fn, { name, capabilityId: "<id>", intent, expectations, referenceTrajectory })` |
| `workflow` (a mode)                | `observe(fn, { name, asType: "chain", expectations })`                                   |
| `tool`                             | `observe(fn, { name: "<tool name>", asType: "tool", expectations })`                     |
| `retrieval`                        | `observe(fn, { name, asType: "retriever", expectations })`                               |
| `function`, `llm_utilities` helper | `observe(fn, { name, expectations })`                                                    |

```ts
import { observe } from "overmind";

export const handleTicket = observe(
  async (message: string) => {
    /* original body, unchanged */
  },
  {
    name: "handleTicket",
    capabilityId: "<capability id from the placement>",
    intent: "<card task>",
    expectations: ["Cites the order id"],
    referenceTrajectory: ["lookup_order", "issue_refund"],
  },
);
```

Rules that keep the chassis, the sync and live scoring consistent:

- **Keep the symbol's name and put the body inline.** Replace the definition with
  `const <same name> = observe(<the original function literal>, {...})`. Callers, the
  card's anchors and the qualname stay valid; types and behavior do not change. A
  wrapper around a function reference (`observe(impl, {...})`) is not in the
  chassis inventory and sync does not read its options. Define the wrapper before
  module-level code that calls it at load time (a `const` is not hoisted).

- **Declare at module level or as an object member.** `overmind chassis` and
  `overmind sync` read `const x = observe(() => {...}, { ... })` and
  `{ x: observe(() => {...}, { ... }) }` members. A wrapper inside a function, a
  class field or a method call is not read: move the logic into a module-level
  wrapped function that the method calls.

- **Options must be literals.** `expectations`, `capabilityId` and `asType` as string
  or array-of-string literals. A variable or spread is invisible to sync and is reported
  as dynamic.

- **`name` is the span name.** Set it to the symbol name, and for tools to the exact
  tool name in `tool_spec`, because trajectory metrics and `referenceTrajectory` match
  on it. In the toml's `eval_matrix`, `span_name` is that `name`.

- **Entry points only get `capabilityId`.** Descendants inherit it. Do not stack two
  wrappers on one function; use the most specific kind, entry point first.

- **Expectation wording** follows setup.md: one checkable sentence each, under 1000
  characters, at most 5 per wrapper, no values or secrets.

- **Input capture.** `observe` records the call's arguments and the return value. For a
  method whose request lives on the instance, pass it as an argument of the wrapped
  function instead; never record a secret or a client handle.

- **Model calls.** The package does not patch provider clients. Wrap each call in a
  small function with `asType: "generation"` and report what the provider returned:

  ```ts
  import { observe, updateActiveObservation } from "overmind";

  export const callModel = observe(
    async (messages: ChatMessage[]) => {
      const reply = await client.chat.completions.create({ model: MODEL, messages });
      updateActiveObservation(
        {
          model: MODEL,
          usageDetails: { input: reply.usage?.prompt_tokens, output: reply.usage?.completion_tokens },
        },
        { asType: "generation" },
      );
      return reply;
    },
    { name: "callModel", asType: "generation" },
  );
  ```

  The Vercel AI SDK's `experimental_telemetry` is not an Overmind integration; use the
  wrapper.

- **Initialise once**, at the process entry, before the first wrapped call:
  `import { init } from "overmind"; init();`. It reads `OVERMIND_API_KEY`,
  `OVERMIND_API_URL`, `OVERMIND_CAPABILITY_ID` and `OVERMIND_PROJECT_ID`; with no key,
  tracing stays off. Never write the key into source or a committed file. For a library
  with no process entry, change nothing and report that the host must call `init()`.
  Short-lived scripts and serverless handlers `await flush()` before exiting.

- **Sessions.** `propagateAttributes({ sessionId }, fn)` groups spans into a
  conversation (`conversation.id`).

After wrapping, run `python -m overmind sync` through the same runner, as in setup.md,
and read `eval_preload.counts.expectations`. Zero after wrapping means the entry
point's `capabilityId` is missing or not the synced id, or its options are not literals.

## Verification

Same approval gate and server-side check as [telemetry.md](telemetry.md). Differences:
stamp the correlation with `propagateAttributes({ sessionId: <correlation> }, () => run())`,
run through the project's own runtime and package manager, and `await flush()` or
`shutdown()` before the process exits so the batch exporter sends the spans. A
typecheck (`tsc --noEmit`, or the repository's own script) is the local check to report
beside the changed files.

## Progress wording

The stage "Instrument capabilities" is complete when the `observe` wrappers are applied
and synced. Name the changed files as setup.md does; say "wrappers" where it says
"decorators".
