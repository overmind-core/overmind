# Lead Qualifier

Classifies inbound sales leads into `hot` / `warm` / `cold` using CRM
enrichment and light web research.

**Stack:** OpenAI SDK (function calling) + EXA + local CSV CRM.

## Register

```bash
overmind agent register lead-qualifier agent:run
overmind agent validate lead-qualifier --data data/seed.json
overmind setup lead-qualifier
overmind optimize lead-qualifier
```

## Seeded sub-optimalities (what Overmind should fix)

- System prompt has no calibration bands (hot/warm/cold thresholds).
- `lookup_company_size` / `exa_search_company` tool descriptions are one-liners.
- Defaults to `gpt-4o` — this is a simple classifier, a smaller model will do.
- No output schema enforcement (uses best-effort JSON parsing).
- Calls `exa_search_company` even when CRM lookup already answered the question.

## Input / output

```python
run(
    {
        "company_name": "Acme Corp",
        "contact_role": "VP Engineering",
        "inquiry_text": "We need enterprise pricing for 400 seats...",
    }
)
# -> {"category": "hot", "lead_score": 88, "reasoning": "...", "next_action": "schedule_call"}
```
