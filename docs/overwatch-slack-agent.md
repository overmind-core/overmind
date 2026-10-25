# Overwatch Slack product scope

Overwatch is Overmind's user insights product: the dashboard is its main
analytics interface, and its Slack agent answers focused product and customer
questions. Overlord remains the broader company agent. The two agents have
different entry points and will share governed analytics through Overwatch MCP;
they must not grow separate copies of the same customer-analytics logic.

See [Overwatch and Overlord MCP architecture](overwatch-overlord-mcp.md) for the
planned integration boundary.

## What it does

### Ask questions

Team members can ask Overwatch about customer activity in a private DM or with
the `/overwatch` slash command. It answers from aggregate Overmind product data
and PostHog usage data, with links or definitions where the underlying data
supports them. It should handle questions such as:

- How many people signed up or were active this week?
- How much did users spend or top up, and what are their subscription and balance patterns?
- Where are users dropping out of the activation funnel?
- Which MCP tools are seeing use, and how has that changed?
- Which customers reached a particular product milestone?

The agent's data tools enforce the boundary. Private DMs and slash commands can
look up individual customer activity by exact email. Public channel messages are
not handled as questions. The model does not query the database or PostHog
directly.

### Follow funnel progress

Each activation milestone has a Slack channel. Overwatch posts a short event
when a user first reaches that step, so the team can follow product activity as
it happens. The initial funnel follows the milestones already represented in
the dashboard:

1. Signed up
1. Joined a project
1. Sent a first trace
1. Synced capabilities
1. Ran an evaluation
1. Started training
1. Deployed a model
1. Made a first model call

The Overmind workspace now has public channels for these steps:
`#overwatch-signed-up`, `#overwatch-project-created`, `#overwatch-first-trace`,
`#overwatch-capabilities-synced`, `#overwatch-evaluations`,
`#overwatch-training`, `#overwatch-model-deployments`, and
`#overwatch-first-model-call`. The channel ID map is supplied privately through deployment settings; `.env.example` contains no workspace IDs.
The existing `#overwatch-project-created` channel is mapped to the
`joined_project` step because the source records project membership, not who
created the project.
Project activity after a user joins is attributed to each member of that project;
the underlying records do not identify which member performed the action. Slack
messages identify a dashboard user reference and the project milestone, not an email.

Channel IDs are configured per step. Delivery is recorded with a stable
user-and-step key and Slack client message ID to suppress ordinary retry
duplicates. If Slack accepts a post and the worker crashes before recording
success, a duplicate is still possible. The rollout timestamp prevents
historical activity from flooding channels when the monitor is first enabled.

## Data sources

- Overmind database: identity, projects, traces, capabilities, evaluations,
  training jobs, deployments, and model calls.
- PostHog: aggregate CLI completions and command usage, active users, MCP tool
  usage, MCP call failures, and unique users at each Overwatch funnel milestone.
  The integration reads event aggregates only. Backend funnel events use the
  Console's Clerk identity and contain the milestone and source timestamp, not
  customer email or trace contents.
- Billing ledger and subscriptions: product spend by service, Stripe top-ups, subscription status,
  and credit balances. User pages include spend and payment history alongside product activity.
- Slack: authenticated request surface and event delivery.

## Delivery and rollout

The dashboard, aggregate PostHog view, private `/overwatch` command, and DM
event handler are in the current branch. Both Slack ingress paths verify
signatures and the configured workspace ID, require an explicit internal Slack user allowlist and matching workspace and email domain,
reject restricted, external, deleted and bot accounts, and use bounded read-only analytics tools. The DM handler accepts only direct
messages, ignores bot messages and public channel posts, and deduplicates Slack
event retries. The PostHog personal key and project ID remain optional; when
absent, answers must say the PostHog data is unavailable.

The funnel delivery ledger and scheduler send milestone events to PostHog when
the existing project write token is configured. Slack notifications are
independent and need a bot token and per-step channel IDs. The rollout timestamp must include its timezone and
gates both destinations; leaving it blank disables new funnel delivery. The
PostHog dashboard query also needs a project-query read key and project ID.
Slack's app setup needs the
`/overwatch` command pointed at
`/api/internal/overwatch/slack/command/`, the Events API URL
`/api/internal/overwatch/slack/events/` with the `message.im` event subscribed,
the `commands`, `chat:write`, `chat:write.public`, `im:history`, `users:read`,
and `users:read.email` OAuth scopes. Install or reinstall the Slack app after
granting the scopes, then add the signing secret, bot token, workspace ID, approved Slack user IDs, and explicit model opt-in to
deployment settings. Model calls require zero retention routing; named customer
reports are rendered locally and are never sent back to the model.
Channel creation and live delivery are workspace operations; this code branch
does not create channels or send messages during development.
