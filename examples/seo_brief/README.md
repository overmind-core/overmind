# SEO Content Brief Generator

Produces a SERP-grounded content brief (intent + outline + gaps + FAQs) a
writer can hand off directly.

**Stack:** OpenAI SDK + EXA (SERP) + Jina Reader (competitor text).

## Seeded sub-optimalities

- Prompt says "search the SERP if helpful" (should be mandatory).
- No intent taxonomy — free-text intent descriptions in the baseline.
- Agent fetches too many URLs.
- Target word count is model-guess, not SERP-calibrated.
- No bound on duplicate fetches.
- JSON is lightly parsed.

## Register

```bash
overclaw agent register seo-brief examples.seo_brief.agent:run
overclaw setup seo-brief
overclaw optimize seo-brief
```
