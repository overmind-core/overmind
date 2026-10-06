# Onboarding progress and data disclosure

Use this presentation for [onboard.md](onboard.md) and [setup.md](setup.md).
Show it in the conversation, using the coding agent's native task list as well
when available. Shell output alone is not a progress update.

## Opening message

Lead with one or two repository-specific sentences: the repository name,
detected package manager, and verified connection state. Use lightweight
manifest/configuration checks; do not read application source before the data
disclosure. Omit unknown details rather than guessing or asking the user for
information the repository supplies. On a rerun, say that the existing project
and capability IDs will be preserved. For example, after verification:

> Found Paper-QA, managed with uv. Your existing Overmind project is connected.
> I'll refresh its capability map and preserve its IDs.

Before starting work, show the complete numbered roadmap below, the current
stage, and the data disclosure. If installation already finished before this
skill became available, mark it complete. After a reload, briefly restate the
roadmap with completed stages marked and continue from the verified position.
An existing project starts decorating at stage 3 once installation and project
connection are verified; do not reinstall or resync just to fill the checklist.

1. **Install Overmind** — add the SDK and install the coding-agent skill.
1. **Connect project** — create or connect the project and configure MCP; reload the coding agent once.
1. **Find entry points** — locate product AI handlers, CLI commands, and agent classes.
1. **Decorate capabilities** — add `@capability` / `@observe` / `task()` at those sites.
1. **Check literals** — keep `description`, `prompt`, and `expectations` as literals or module constants.
1. **Sync** — run `overmind sync` (AST scan → AgentManifest push).
1. **Confirm graph** — re-read `overmind://capabilities/{capability}` and the agent graph.

The roadmap ends at a successful capability sync. Instrumentation, running the
application, and completion of asynchronous server-side evaluator preparation
are separate work; do not imply they have happened.

## Stage updates

Use the same names and numbering throughout. At every stage transition, show
the current stage, completed count, and how many stages follow it. Add one
sentence stating a useful finding so far and the current action. Prefer what
the result means for this repository over a diary of commands or raw function
counts. Keep the numbered format; do not add graphical bars or symbol legends.

During a longer stage, repeat that heading with a concrete update after several
tool calls. Prefer counts from completed work, never from started commands.
When discovery finds no entry points, say so and still sync the empty
manifest so the project stays current.

Stage numbers describe workflow position, not time or a percentage of effort.
Do not estimate a duration or advance progress on a timer. Do not print internal
markers such as `__STAGE__`, `__FOUND__`, `__NOTE__`, or JSON findings in chat.

For a blocked stage, keep its number and completed count, label it **Blocked**,
and state the failure and next action. If sync fails after the upload but before
reconciliation, say the local sync did not finish; do not claim nothing reached
the server. On resume, verify earlier outputs before marking them complete.

## Outcome and next action

After sync succeeds, finish with **Onboarding complete · 7 of 7 stages**.
Give the user a compact outcome, not another recap of the steps:

- What landed: capability names/count and destination. Count tools only when
  the sync response or capability resource confirms them.
- One useful source-grounded finding, when available, and any material caveat
  such as unresolved decorator kwargs reported by sync.
- What has not been verified: instrumentation, a real application run, and
  asynchronous evaluator readiness are separate from a successful sync.
- One recommended next action based on the remaining gap. For example, when
  tracing has not been checked: "Next: inspect instrumentation coverage before
  running an example question." Recommend it; do not automatically instrument,
  run the application, or start evaluations as part of onboarding.

On a rerun, report preserved identities or no capability changes when verified.
Use factual, repository-specific copy without celebration, marketing claims,
or artificial delays.

## Data disclosure

Before reading application source, show these facts in a short, concrete block.
Resolve the actual API destination from the CLI override, `OVERMIND_API_URL`,
or `overmind.toml`, in that precedence order. Display the destination without
credentials or query parameters. Name the coding-agent provider/model only
when the host exposes it; otherwise say **Selected in your coding agent; exact
model not exposed to this session**. A model ID found in the customer's code
is not the model performing the scan.

- **Coding-agent model:** repository excerpts and tool results read during
  decoration enter the coding agent's context and may be sent to its configured
  provider. The AST scan itself does not call a model; card authoring happens
  server-side after sync.
- **Overmind destination:** name the resolved API host and project. Sync sends
  the AgentManifest (declared symbols, signatures, literal prompts/expectations,
  call-graph edges, repository fingerprint). It does not send a repository archive.
- **Credentials:** the CLI uses the Overmind API key to authenticate to that host.
  Do not read secret files into model context or copy credential values into
  decorator arguments.
- **After sync:** Overmind derives the capability card and prepares evaluators
  with its configured model providers. If this deployment's exact models/providers
  are not available, say **Server evaluator models not available in this session**.

Before the final sync, check decorated source for accidentally copied secrets
without echoing their values. Then give a compact handoff:

- **Destination:** the resolved API host, project, and capability count.
- **Included:** declared symbols, signatures, prompts, tools, repository fingerprint.
- **Credential checks:** state which checks ran and their result.
- **Review:** a link to the decorated source files (not a capability toml).

Show the full disclosure at the start and this handoff before upload, not at
every stage. Surface material changes in destination or data scope before
sending; if they exceed the user's authorization, pause for direction. Do not
add a routine confirmation prompt for an already-authorized sync. Respect any
user restriction on data transfer; disclosure does not override it.

Do not promise that only function schemas leave the machine, or that source
never enters a model. Do not add certification, retention, residency, or
no-training claims unless current evidence for the relevant deployment is
available. If asked, identify what is verified and what still needs evidence.
