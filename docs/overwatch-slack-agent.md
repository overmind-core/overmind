# Overwatch Slack product scope

Overwatch is the product and customer insights specialist, with a dashboard and
private Slack Q&A. Overlord is the broader company agent. The proposed shared
runtime is described in [the MCP architecture](overwatch-overlord-mcp.md).

## Read-only product access

Overwatch reads identity, membership, traces, capabilities, evaluations, training,
deployments, inference, spend, payments, subscriptions and balances. It must not
create, modify or delete Overmind product records. Spend and payments remain
part of the dashboard and customer context.

PostHog supplies read-only behavioral aggregates and ordered conversion funnels.
PostHog identities can include unlinked installations and are not automatically
registered customer counts. Database milestones show independent adoption,
with project activity attributed to members after they joined, not to an
identified actor.

The current branch contains the dashboard, aggregate PostHog reads, private
`/overwatch` command and DM handler. Normal customer/project credentials cannot
access the internal dashboard endpoints. A future service credential must grant
analytics reads only, with no product write capabilities.

## Ask questions

Approved employees can ask about usage, milestones, customer activity, spend and
payments in a DM or slash command. Both ingress paths verify Slack signatures,
workspace and the user allowlist before queueing. Workers recheck the employee
profile and reject deleted, restricted, external and bot accounts. Public channel
questions are ignored. Supplied emails become local references before model
calls; named customer reports are formatted locally and never returned to the
model. Aggregate answers require tool evidence and explicit source/window limits.

Configure the command at `/api/internal/overwatch/slack/command/` and subscribe
to `message.im` at `/api/internal/overwatch/slack/events/`. Use `commands`,
`chat:write`, `im:history`, `users:read` and `users:read.email` scopes. Supply the
bot token, signing secret, workspace, allowed employee IDs and explicit model
opt-in privately. PostHog query access requires a project-bound read key; never
put it in browser variables. Model routing requires zero retention.

## Funnel notifications: proposed

The intended channels follow signup, project membership, first trace, capability
sync, evaluation, training, deployment and first model call. Prefer native
PostHog Slack destinations for existing events, adding a workflow when needed.
Verify each milestone's event coverage, identity, successful outcome and duplicate
handling before activation. Missing events are not evidence of zero conversions.
Any extra Overwatch notification state belongs outside the product backend.

The earlier product-database delivery prototype is archived in
[proposals](proposals/README.md). No delivery model, migration, periodic writer
or event emitter remains active in the product backend. Notification delivery
and the Overlord/MCP integration require Pritam's implementation review.
