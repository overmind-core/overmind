# Research Brief (multi-agent)

SEO content brief generator. Multi-agent: **Researcher → Outliner → Editor**.

- **Researcher** (OpenAI, function-calling) hits `web_search`, `fetch_url`,
  `keyword_metrics_lookup`. Real EXA when `EXA_API_KEY` is set, otherwise a
  local stub.
- **Outliner** (OpenAI) turns notes into an outline.
- **Editor** (OpenAI) produces the final structured brief.

## Register

```bash
overmind agent register research-brief agent:run
overmind agent validate research-brief --data data/seed.json
overmind setup research-brief
overmind optimize research-brief
```

## Seeded sub-optimalities

- Researcher pulls 20 sources and summarises everything — wasteful. Overmind
  should cap at 5–8 with a relevance filter.
- Outliner and Editor both default to `gpt-4o`. Both are downgradable.
- No audience-persona block in any prompt — outputs read generic.
- Stages communicate via free-text Markdown. Overmind can impose at least a
  loose JSON contract for the researcher → outliner hand-off.
- `keyword_metrics_lookup` description is a one-liner; model uses it
  inconsistently.
- No JSON schema enforcement on the final brief.

## Input / output

```python
run(
    {
        "topic": "RAG pipeline best practices",
        "target_audience": "ML engineers shipping production RAG",
        "primary_keyword": "rag pipeline",
    }
)
# -> {
#   "title_options": [...],
#   "target_keywords": ["rag pipeline", "rag chunking", ...],
#   "outline": [{"heading": "...", "bullets": [...]}, ...],
#   "faqs": [{"q": "...", "a": "..."}, ...],
#   "internal_link_suggestions": [...],
#   "sources": ["https://..."]
# }
```
