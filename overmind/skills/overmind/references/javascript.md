# JavaScript and TypeScript repositories

Read this file when the repository to onboard is JavaScript or TypeScript. The
workflow in [setup.md](setup.md) is the same: `overmind chassis`, the capability
snapshot, conversion to `overmind.toml` and `overmind sync`. Instrumentation does not
apply: see [No instrumentation](#no-instrumentation). This file lists where it differs.
Where it is silent, follow setup.md.
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

## No instrumentation

Tracing is allowed only for a Python codebase. A JavaScript or TypeScript repository
gets no telemetry: set up no OpenTelemetry exporter, point no OTLP endpoint at Overmind,
install no Overmind package, stamp no `overmind.*` attributes and apply no edits to
its code or dependency manifest. `get_instrumentation_plan` returning anchors for these
capabilities does not override this.

- Skip setup.md's **Instrument** stage and the verification run in
  [Observability](../../overmind-observability/SKILL.md). Scanning, conversion and both syncs still apply, so the
  capabilities and the toml's `eval_matrix` checks exist; there are no decorator
  expectations.
- Report "Instrument capabilities" as not applicable, naming the language found. Do not
  report it as pending or complete.
- A repository that holds Python and JavaScript/TypeScript application code is not a
  Python codebase: it gets no tracing either.
- Datasets, evaluation, training and inference do not depend on tracing and stay
  available.
