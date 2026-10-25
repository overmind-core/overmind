# Overwatch and Overlord MCP architecture

## Decision

Keep Overwatch and Overlord as separate agents with clear roles:

- **Overwatch** owns product and customer insights. Its dashboard is the main
  analytics interface. Its Slack agent handles focused analytics questions and
  posts funnel milestones to the configured channels.
- **Overlord** remains the wider company agent. It answers in its existing Slack
  experience and can combine PostHog event analysis with customer and commercial
  context from Overwatch.
- **Overwatch MCP** will be the read-only analytics interface Overlord uses
  when it needs customer or product context. Its tools will call the same
  canonical services as the dashboard and Overwatch Slack agent, so it is not a
  second source of analytics logic.

This preserves a specialist Overwatch experience while allowing Overlord to
answer broader questions. Keep the Slack entry points distinct, `/overwatch` for
product insights and the existing Overlord mention or command for general
questions. Funnel alerts remain in the Overwatch channels.

## Data flow

```text
Overmind database ─┐
Billing ledger ────┼─> shared Overwatch services ─┬─> Overwatch dashboard
PostHog aggregates ┘                              ├─> Overwatch Slack agent
                                                  └─> Overwatch MCP ─┐
PostHog MCP ─────────────────────────────────────────────────────────┴─> Overlord Slack agent
```

Overwatch services define the canonical customer identity, funnel milestones,
spend, payments, subscription, balance and product-usage views. The dashboard,
Overwatch Slack tools and Overwatch MCP should call those services. The
Overwatch MCP must not call the dashboard's internal REST endpoints.

Overlord can use both MCPs for different needs:

- Use **Overwatch MCP** for a customer profile, funnel status, activation
  milestones, spend, payments, subscription and credit balance.
- Use **PostHog MCP** for event-level exploration, behavioural breakdowns and
  questions that need PostHog's own event or insight definitions.
- When a question spans both sources, join only on the stable identity mapping
  already used by Overwatch. Return source and time window with the answer. If
  the sources disagree, show the two values and their definitions rather than
  silently choosing one.

PostHog remains an input to the Overwatch dashboard. Sending funnel milestones
to PostHog and to Slack remains independent of Overlord's query path.

## Tool and access boundary

The Overwatch MCP should expose a small, read-only contract, such as:

- `get_customer_insights`: exact customer lookup with product activity and
  commercial context.
- `query_product_usage`: bounded aggregate metrics over a time window.
- `get_funnel_progress`: counts and conversion between named milestones.
- `get_customer_milestones`: milestone history for one customer.

Keep spend and payment fields in customer insights. Return only fields needed to
answer the question, include source timestamps, and cap result sizes. Funnel
alerts and aggregate answers must not include customer email or trace contents.
Customer-level details should only be available in approved private Slack
contexts.

The existing `/api/mcp/` serves Overmind customer projects and account
credentials. Company-wide Overwatch data crosses customer project boundaries,
so it must have an explicit internal employee or service identity and a
separate read-only permission. Ordinary customer API keys and OAuth grants must
never discover or call these tools. Reuse the existing MCP transport and
catalog; do not mount another MCP server. The current dashboard bearer token is
not, by itself, proof that a caller may query cross-customer data.

The implementation should record the caller and tool name, but not customer
identifiers, event payloads, or returned records in its audit log. Add tests
showing customer credentials cannot see or invoke internal analytics tools,
while the approved Overlord service identity can.

## Ownership for the review

This branch owns the Overwatch dashboard, shared analytics services, funnel
delivery and the Overwatch MCP contract. The Overlord repo owns its Slack
experience, MCP client configuration, tool selection and answer composition.
Its current worktree has unrelated uncommitted changes, so this branch does not
modify it.

This branch documents the MCP contract; it does not yet expose an MCP endpoint.
Before wiring Overlord to a deployed endpoint, Pritam should confirm the
internal service identity and how it will be provisioned. The contract above
keeps the data boundary clear without binding the integration to customer
credentials or the dashboard's legacy token.
