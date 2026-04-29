# Support Ticket Triage

Routes inbound support tickets: classifies, prioritises, flags escalation,
drafts a first reply. Uses an internal KB, a customer lookup, and public docs.

**Stack:** Anthropic SDK (native tool use) + local JSON KB + EXA fallback.

## Seeded sub-optimalities

- No priority rubric in the prompt (model over-uses P1/P0).
- Identical one-liner descriptions on all three tools.
- Prefers web search over the internal KB.
- Uses Claude Sonnet for a classification task.
- Tone not calibrated to customer tier.
- JSON output parsed with best-effort fallback.

## Register

```bash
overmind agent register support-triage agent:run
overmind agent validate support-triage --data data/seed.json
overmind setup support-triage
overmind optimize support-triage
```
