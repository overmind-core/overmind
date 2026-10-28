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
Only aggregates and sanitized questions reach the model.
Overlord's HR tools, saved conversations, extraction and training traces are
excluded. Short DM-thread follow-ups replay only the caller's earlier questions,
never bot reports. Audit contains tool metadata only. No content is kept in the
external Redis admission store; it holds only retry hashes, Slack IDs and limits.

Model use is disabled by default. The Anthropic model is called through
OpenRouter with zero-data-retention routing required on every request.
Email/credential filters cannot classify all sensitive prose. Pritam must approve
that data path before enabling it.

## Deployment keys

Each environment has its own values.

Overmind API (staging and production ECS tasks):

| Variable                   | Value                                                         |
| -------------------------- | ------------------------------------------------------------- |
| `OVERWATCH_SERVICE_TOKEN`  | `openssl rand -hex 32`; distinct from `SALES_DASHBOARD_TOKEN` |
| `SALES_DASHBOARD_TOKEN`    | Dashboard bearer; the dashboard routes exist only when set    |
| `POSTHOG_PERSONAL_API_KEY` | PostHog personal API key with `query:read` on the project     |
| `POSTHOG_PROJECT_ID`       | Numeric PostHog project ID                                    |
| `POSTHOG_API_HOST`         | Optional; defaults to `https://eu.posthog.com`                |

Without the PostHog pair, product usage reports PostHog as unavailable and the
database aggregates still answer.

Overlord deployment:

| Variable                                                 | Value                                                                |
| -------------------------------------------------------- | -------------------------------------------------------------------- |
| `OVERWATCH_SLACK_BOT_TOKEN`                              | `xoxb-` token after reinstalling with the Q&A scopes                 |
| `OVERWATCH_SLACK_SIGNING_SECRET`                         | Slack app Basic Information                                          |
| `OVERWATCH_SLACK_APP_ID`                                 | `A...`                                                               |
| `OVERWATCH_SLACK_TEAM_ID`                                | `T...`                                                               |
| `OVERWATCH_SLACK_ALLOWED_USER_IDS`                       | Comma-separated `U...` IDs with linked employee accounts             |
| `OVERWATCH_ANALYTICS_TOKEN`                              | Same value as that environment's `OVERWATCH_SERVICE_TOKEN`           |
| `OVERWATCH_API_URL`                                      | `https://api-staging.overmindlab.ai` or `https://api.overmindlab.ai` |
| `OVERWATCH_OPENROUTER_API_KEY`                           | OpenRouter key                                                       |
| `OVERWATCH_AGENT_MODEL`                                  | `anthropic/claude-opus-4.8`                                          |
| `OVERWATCH_REDIS_REST_URL`, `OVERWATCH_REDIS_REST_TOKEN` | Dedicated Upstash Redis per environment                              |
| `OVERWATCH_ALLOWED_EMAIL_DOMAIN`                         | `overmindlab.ai`                                                     |
| `OVERWATCH_AGENT_ENABLED`                                | `false` until activation                                             |

A Slack app has one request URL per route, so staging Slack ingress needs its own
app. The dashboard deploys to Cloudflare Pages on a published release, alongside
the production API.

## Funnel notifications

Prefer native PostHog Slack destinations for verified existing events, adding a
workflow when needed. Check event coverage, identity, successful outcome,
first-time/repeat policy and duplicates before activation. Database adoption
counts are independent milestones, with project activity attributed to members,
not an identified actor. PostHog identities may include unlinked installations.
Missing events are not zero conversions. Notifications are a separate setup
step and require no changes to core Overmind. Neither PR enables live delivery.

Companion Slack runtime PR: [overlord#8](https://github.com/overmind-core/overlord/pull/8).
