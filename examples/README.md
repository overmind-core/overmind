# OverClaw Example Agents

A curated set of **deliberately sub-optimal** agents across different industries,
agent patterns, and tool mixes. Each one is intended as a showcase for running
`overclaw setup` + `overclaw optimize` and watching scores improve.

Every agent exposes a single entrypoint:

```python
def run(input_data: dict) -> dict: ...
```

so you can register it with:

```bash
overclaw agent register <name> examples.<folder>.agent:run
```

## The portfolio

| #   | Folder                                        | Industry         | Pattern                    | Models                 | External APIs             | Files  |
| --- | --------------------------------------------- | ---------------- | -------------------------- | ---------------------- | ------------------------- | ------ |
| 1   | [`lead_qualifier/`](./lead_qualifier)         | Sales / SaaS     | Classifier + enrichment    | OpenAI                 | EXA + local CSV           | multi  |
| 2   | [`support_triage/`](./support_triage)         | Customer Support | Router + response drafter  | Anthropic              | Local KB + EXA            | multi  |
| 3   | [`investment_memo/`](./investment_memo)       | Finance          | Multi-step researcher      | OpenAI                 | EXA + Jina + SEC EDGAR    | multi  |
| 4   | [`clinical_evidence/`](./clinical_evidence)   | Healthcare       | Evidence-graded researcher | Anthropic              | PubMed + EXA + Jina       | multi  |
| 5   | [`contract_extractor/`](./contract_extractor) | Legal            | Long-context extraction    | OpenAI                 | _none_                    | single |
| 6   | [`incident_triage/`](./incident_triage)       | DevOps / SRE     | Correlation + calibration  | OpenAI (via LangChain) | GitHub API + mock Datadog | multi  |
| 7   | [`seo_brief/`](./seo_brief)                   | Marketing        | SERP-grounded synthesis    | OpenAI                 | EXA + Jina                | multi  |

## What OverClaw is expected to improve on each

|            | Prompt | Tool descs | Model choice  | Tool ordering | Iter cap | Schema | Policy |
| ---------- | ------ | ---------- | ------------- | ------------- | -------- | ------ | ------ |
| 1 Lead     | x      | x          | x (downgrade) |               |          | x      | x      |
| 2 Support  | x      | x          | x (downgrade) | x             |          | x      | x      |
| 3 Invest   | x      | x          |               | x             | x        | x      | x      |
| 4 Clinical | x      | x          | x (upgrade)   | x             | x        | x      | x      |
| 5 Contract | x      |            |               |               |          | x      | x      |
| 6 Incident | x      | x          |               | x             |          | x      | x      |
| 7 SEO      | x      | x          |               | x             | x        | x      | x      |

## Running

```bash
cp ../.env.example .env      # add OPENAI_API_KEY, ANTHROPIC_API_KEY, EXA_API_KEY
pip install -r examples/lead_qualifier/requirements.txt
```

Then from the repo root:

```bash
overclaw agent register lead-qualifier examples.lead_qualifier.agent:run
overclaw setup lead-qualifier
overclaw optimize lead-qualifier
```

(Agent **names** can be anything — they're just registry keys. The folder
name must be a valid Python identifier, so we use `lead_qualifier` on disk
and `lead-qualifier` as the pretty registry name.)

## Design notes

- No `policies.md` ships with these examples — `overclaw setup` will infer
  one from the agent code, or you can drop in your own via `--policy`.
- Baselines are intentionally leaky (vague prompts, thin tool docstrings, wrong
  model tier, unbounded iteration) — but in realistic ways. They aren't
  straw-manned.
- Seed datasets (`data/seed.json`) are small (5-10 cases). Let
  `overclaw setup` synthesise more from the policy.
- External API calls can be mocked via `OVERCLAW_USE_FIXTURES=1` where
  fixtures are provided (currently only `incident_triage`).
