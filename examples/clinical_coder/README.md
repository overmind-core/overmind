# Clinical Coder

Codes a clinical encounter with ICD-10 + CPT, decides whether prior auth is
required, and scores denial risk.

**Stack:** OpenAI SDK (function calling) + local JSON code books + payer
policy fixtures.

## Register

```bash
overmind agent register clinical-coder agent:run
overmind agent validate clinical-coder --data data/seed.json
overmind setup clinical-coder
overmind optimize clinical-coder
```

## Seeded sub-optimalities (what Overmind should fix)

- System prompt has no calibration bands for `denial_risk` (just "0-100, be careful").
- Tool descriptions are one-liners; nothing tells the model to skip the payer
  call for routine E/M codes.
- Always calls all four tools, even when the procedure is an obvious office visit.
- Defaults to `gpt-4o` — most cases are a small classifier and a single lookup.
- No output schema enforcement; `modifiers` comes back as free text.
- No PHI-handling / redaction guardrail in the system prompt.

## Input / output

```python
run(
    {
        "member_id": "M-1001",
        "payer": "aetna",
        "procedure": "MRI lumbar spine without contrast",
        "clinical_note": "62yo F with 8 weeks of low back pain...",
    }
)
# -> {
#   "icd10": ["M54.5"],
#   "cpt": ["72148"],
#   "modifiers": [],
#   "auth_required": true,
#   "denial_risk": 25,
#   "reasoning": "..."
# }
```
