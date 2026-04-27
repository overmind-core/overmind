# Contract Clause Extractor

Extracts parties, term, termination, liability, IP assignment, renewal
behaviour, and red flags from a contract — entirely from long-context LLM
inference (no tools).

**Stack:** OpenAI SDK (chat completions). Intentionally single-file — this is
the "OverClaw helps even when you have no tools" demo.

## Seeded sub-optimalities

- Prompt doesn't require evidence spans.
- No date-format normalisation rule.
- `red_flags` taxonomy is unspecified, so the model invents ad-hoc labels.
- Missing fields come back as mixed `null` / `"N/A"` / `""`.
- Uses `gpt-4o` for all documents - could be tiered by length.

## Register

```bash
overclaw agent register contract-extractor examples.contract_extractor.agent:run
overclaw setup contract-extractor
overclaw optimize contract-extractor
```
