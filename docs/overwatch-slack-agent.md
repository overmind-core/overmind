# Overwatch Slack product scope

Overwatch is the usage and customer insights dashboard and specialist Slack
agent. It uses Overlord's shared engine, with its own Slack identity and a fixed
analytics-only profile. The implementation spans this PR and a companion PR in
`overmind-core/overlord`. See [the architecture](overwatch-overlord-mcp.md) and the
companion repository's `docs/overwatch.md` for setup and operational limits.

## Read-only product access

Five queries cover adoption, usage, billing, milestone lists and private customer
reports. Spend, payments, subscriptions and credit balances remain part of the
product. `POST /api/internal/overwatch/agent/query/` accepts a dedicated
`OVERWATCH_SERVICE_TOKEN`, distinct from the dashboard bearer. POST keeps private
arguments out of URLs; the database transaction itself is read-only. Statement
and report budgets apply. This key cannot authenticate product writes or the
dashboard; ordinary user/project keys and staff sessions cannot use the agent
endpoint. It is excluded from public OpenAPI and is not a new MCP server.

There are no active Overwatch product models, migrations, scheduled writers,
backend event emitters or shared dependency changes. The old standalone model
loop and Slack/Celery adapter are archived as non-executable examples under
[retired adapter](proposals/retired-slack-adapter/README.md).

## Private Slack behavior

The companion runtime supplies signed `/overwatch` and `message.im` routes in
Overlord's deployment, using separate app/workspace IDs, bot token and signing
secret. The explicit Slack user allowlist grants analytics access; HR admins do
not bypass it. Before querying, the live Slack profile must be unrestricted,
internal and linked to an existing employee. Public mentions are ignored and
slash replies are ephemeral. No live app is installed by either PR.

Emails become request-local references. Named customer reports are rendered by
this backend and terminate the shared loop without returning to the model.
Only aggregates and sanitized questions reach the dedicated Anthropic client.
Overlord's HR tools, saved conversations, extraction and training traces are
excluded. Short DM-thread follow-ups replay only the caller's earlier questions,
never bot reports. Audit contains tool metadata only. No content is kept in the
external Redis admission store; it holds only retry hashes, Slack IDs and limits.

Model use is disabled by default. Deployment requires an approved Anthropic key
and retention agreement. The direct client does not enforce OpenRouter-style
zero-retention routing, and email/credential filters cannot classify all
sensitive prose. Pritam must approve that data path before enabling it.

## Funnel notifications

Prefer native PostHog Slack destinations for verified existing events, adding a
workflow when needed. Check event coverage, identity, successful outcome,
first-time/repeat policy and duplicates before activation. Database adoption
counts are independent milestones, with project activity attributed to members,
not an identified actor. PostHog identities may include unlinked installations.
Missing events are not zero conversions. Notifications are a separate setup
step and require no changes to core Overmind. Neither PR enables live delivery.

Companion Slack runtime PR: [overlord#8](https://github.com/overmind-core/overlord/pull/8).
