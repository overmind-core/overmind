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
An existing project starts scanning at stage 3 once installation and project
connection are verified; do not reinstall or resync just to fill the checklist.

1. **Install Overmind** — add the SDK and install the coding-agent skill.
1. **Connect project** — create or connect the project and configure MCP; reload the coding agent once.
1. **Inspect repository** — build a local inventory of functions and entry points.
1. **Discover capabilities** — identify the distinct AI purposes in the repository.
1. **Describe capabilities** — capture each capability's prompts, tools, inputs, and outputs.
1. **Verify source references** — check the descriptions against the source.
1. **Prepare evaluations** — draft the starter evaluation criteria.
1. **Write snapshot** — save the capability descriptions incrementally.
1. **Validate configuration** — convert and check the local `overmind.toml`.
1. **Sync capabilities** — send the snapshot to the configured Overmind project.

The roadmap ends at a successful capability sync. Instrumentation, running the
application, and completion of asynchronous server-side evaluator preparation
are separate work; do not imply they have happened.

## Stage updates

Use the same names and numbering throughout. At every stage transition, show
the current stage, completed count, and how many stages follow it. Add one
sentence stating a useful finding so far and the current action. Prefer what
the result means for this repository over a diary of commands or raw function
counts. Keep the numbered format; do not add graphical bars or symbol legends.
For example:

```text
Stage 5 of 10 · Describe capabilities
4 complete · 5 stages after this

Found one capability: Paper question answering. Its three execution modes share
the same evidence and citation workflow. Checking their prompts and tools.
```

During a longer stage, repeat that heading with a concrete update after several
tool calls, roughly once a minute while actively working:

```text
Stage 5 of 10 · Describe capabilities
4 complete · 5 stages after this

1 of 2 capability descriptions complete. Paper Q&A uses citations from its
retrieved evidence; checking the second capability's output contract.
```

Use counts from completed work, never from started commands. During discovery,
say how many capabilities have been found so far; the final total is unknown.
Update the total if discovery changes it and revisit affected descriptions.
When discovery finds no capabilities, say so and mark the per-capability stages
as having no work; still validate, convert, and sync the empty snapshot.

At the end of discovery, give a short reveal: name the capabilities in the
repository's own terms and explain one source-grounded relationship, such as
shared state, execution modes, or how evidence reaches an answer. Distinguish
what the code declares from observed runtime behavior. Do not fabricate an
insight to fill the format; for a large result, summarize representative groups
and link the full snapshot once written. Examples here illustrate the voice,
not names or counts to reuse without evidence.

Stage numbers describe workflow position, not time or a percentage of effort.
Do not estimate a duration or advance progress on a timer. Do not print internal
markers such as `__STAGE__`, `__FOUND__`, `__NOTE__`, or JSON findings in chat.
Keep progress out of the snapshot files.

For a blocked stage, keep its number and completed count, label it **Blocked**,
and state the failure and next action. If sync fails after the upload but before
reconciliation, say the local sync did not finish; do not claim nothing reached
the server. On resume, verify earlier outputs before marking them complete.

## Outcome and next action

After sync succeeds, finish with **Onboarding complete · 10 of 10 stages**.
Give the user a compact outcome, not another recap of the steps:

- What landed: capability names/count, destination, and a link to the local
  `overmind.toml`. Count tools and proposed evaluation criteria only when those
  details survived conversion and sync.
- One useful source-grounded finding, when available, and any material caveat
  such as dropped metadata or an unverified path. Never turn a potential risk
  into a confirmed runtime bug without evidence.
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

- **Coding-agent model:** repository excerpts and tool results read during the scan enter the coding agent's context and may be sent to its configured provider. Local AST inventory and TOML conversion do not themselves call a model.
- **Overmind destination:** name the resolved API host and project. The initial connection sends project setup data and any existing snapshot. The final sync sends the repository summary, capability descriptions, source paths/references, captured prompt text, tool descriptions/schemas, and evaluation specifications. It does not send a repository archive.
- **Credentials:** the CLI uses the Overmind API key to authenticate to that host. The configuration's API-key field is excluded from the capability snapshot. Do not read secret files into model context or copy credential values into capability descriptions. Arbitrary prompt/card text is not automatically secret-free.
- **After sync:** Overmind can use the synced capability metadata to prepare evaluators with its configured model providers. If this deployment's exact models/providers are not available, say **Server evaluator models not available in this session**; do not guess from SDK defaults or call this local-only processing.

Before the final sync, check the generated snapshot for accidentally copied
secrets without echoing their values. If one is found, remove the secret value
before upload while retaining the capability description. Then give a compact
handoff using these fields:

- **Destination:** the resolved API host, project, and capability count.
- **Included:** the data categories actually present in the generated snapshot.
- **Credential checks:** state which checks ran and their result, or that they
  were not performed; do not imply arbitrary text is guaranteed secret-free.
- **Review:** a link to the local `overmind.toml`.

Show the full disclosure at the start and this handoff before upload, not at
every stage. Surface material changes in destination or data scope before
sending; if they exceed the user's authorization, pause for direction. Do not
add a routine confirmation prompt for an already-authorized sync. Respect any
user restriction on data transfer; disclosure does not override it.

Do not promise that only function schemas leave the machine, or that source
never enters a model. Do not add certification, retention, residency, or
no-training claims unless current evidence for the relevant deployment is
available. If asked, identify what is verified and what still needs evidence.
