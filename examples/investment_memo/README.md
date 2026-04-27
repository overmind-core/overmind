# Investment Thesis Memo

Research-analyst agent that answers a thesis question on a given ticker,
grounded in SEC filings + news.

**Stack:** OpenAI SDK + EXA (news) + Jina Reader (r.jina.ai) + SEC EDGAR.

## Seeded sub-optimalities

- Iteration cap is 16 - the agent loops through tool calls unnecessarily.
- Prompt never mentions citation rules or the fact/opinion split.
- Tool descriptions don't tell the model when EDGAR is preferable to news.
- Horizon input is ignored in the baseline.
- No URL de-duplication.

## Register

```bash
overclaw agent register investment-memo examples.investment_memo.agent:run
overclaw setup investment-memo
overclaw optimize investment-memo
```
