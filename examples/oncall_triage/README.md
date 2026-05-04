# On-call Triage (multi-agent)

Multi-agent SRE incident triage: **Router → Investigator → Responder**.

- **Router** (OpenAI) reads the raw alert, writes a brief.
- **Investigator** (OpenAI, function-calling loop) digs into logs, metrics,
  runbooks, deploys.
- **Responder** (Anthropic) writes the final structured triage and a public
  status message.

## Register

```bash
overmind agent register oncall-triage agent:run
overmind agent validate oncall-triage --data data/seed.json
overmind setup oncall-triage
overmind optimize oncall-triage
```

## Seeded sub-optimalities

- Router prompt is a rambling paragraph — no concise rubric for what to put in
  the brief.
- Investigator has `_MAX_TOOL_ROUNDS = 12` and no nudge to call `search_runbook`
  first — burns rounds on logs / deploys before reading the runbook.
- Responder uses `claude-opus-4-1` to write a 2-line Slack status. Trivial
  downgrade.
- Hand-off between Router → Investigator → Responder is free-text. Overmind can
  impose a structured contract.
- No SEV rubric anywhere; severity is wildly miscalibrated on seed eval.
- No JSON schema on the final output; `suggested_actions` sometimes a string,
  sometimes a list.

## Input / output

```python
run(
    {
        "alert": {
            "alert_name": "checkout-api 5xx > 5%",
            "service": "checkout-api",
            "environment": "production",
            "fired_at": "2026-04-21T09:02:00Z",
            "summary": "Error rate climbed from 1% baseline to 22% over 7 minutes.",
        }
    }
)
# -> {
#   "severity": "SEV1",
#   "hypothesis": "...",
#   "suggested_actions": ["rollback v3.14.2", "page payments-oncall"],
#   "status_message": "We are investigating elevated errors at checkout.",
#   "escalate": true
# }
```
