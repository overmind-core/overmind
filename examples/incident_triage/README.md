# Incident Triage Copilot

Triages an inbound on-call alert: calibrates severity, correlates with recent
deploys, suggests an owner, and emits next steps.

**Stack:** LangChain tool-calling agent + GitHub API + mock Datadog JSON
fixtures + local runbooks. This one is built with LangChain so you can
exercise OverClaw's framework auto-entrypoint generation flow.

Set `OVERCLAW_USE_FIXTURES=1` to use fixture data for GitHub calls
(deterministic demo, no network).

## Seeded sub-optimalities

- Severity rubric is not defined in the prompt; model over-uses SEV1.
- Agent doesn't always check commits around the alert window.
- `search_runbook` is called with the raw alert text (long/noisy).
- No schema validation on the final JSON.
- Uses `gpt-4o-mini` for severity calibration — may need upgrade.

## Register

```bash
overclaw agent register incident-triage examples.incident_triage.agent:run
overclaw setup incident-triage
overclaw optimize incident-triage
```
