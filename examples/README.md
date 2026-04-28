# OverClaw Example Agents

A curated set of **deliberately sub-optimal** agents across different industries, agent patterns, and tool mixes. Each one is intended as a showcase for running`overclaw setup` + `overclaw optimize` and watching scores improve.

Every agent exposes a single entrypoint:

```python
def run(input_data: dict) -> dict: ...
```

so you can register it with:

```bash
overclaw agent register agent:run
```

## The portfolio

| #   | Folder                                        | Industry         | Pattern                   | Models    | External APIs   |
| --- | --------------------------------------------- | ---------------- | ------------------------- | --------- | --------------- |
| 1   | `[lead_qualifier/](./lead_qualifier)`         | Sales / SaaS     | Classifier + enrichment   | OpenAI    | EXA + local CSV |
| 2   | `[support_triage/](./support_triage)`         | Customer Support | Router + response drafter | Anthropic | Local KB + EXA  |
| 3   | `[contract_extractor/](./contract_extractor)` | Legal            | Long-context extraction   | OpenAI    | *none*          |

## Optimization runs (example results)

Scores are from a representative `overclaw optimize` run (train-side summary in each agent’s `report.md`). **Final score** is the reported best average (same as the **Best** column in `report.md`).

| Example            | Baseline | Final score | Improvement |
| ------------------ | -------- | ----------- | ----------- |
| lead_qualifier     | 52.5     | 73.9        | +21.4       |
| contract_extractor | 74.3     | 94.1        | +19.8       |
| support_triage     | 57.9     | 64.7        | +6.8        |

## What OverClaw is expected to improve on each

|          | Prompt | Tool descs | Model choice  | Tool ordering | Iter cap | Schema | Policy |
| -------- | ------ | ---------- | ------------- | ------------- | -------- | ------ | ------ |
| Lead     | x      | x          | x (downgrade) |               |          | x      | x      |
| Support  | x      | x          | x (downgrade) | x             |          | x      | x      |
| Contract | x      |            |               |               |          | x      | x      |

## Running

```bash
cd examples/lead_qualifier
overclaw agent register lead-qualifier agent:run
overclaw agent validate lead-qualifier --data data/seed.json
overclaw setup lead-qualifier --data data/seed.json
overclaw optimize lead-qualifier
```

Run **validate** before **setup** so you can smoke-test the entrypoint on seed data. Paths are relative to the repo root; if you work inside an example folder instead, adjust accordingly (e.g. from `examples/support_triage/` with the agent registered as `tria`: `overclaw agent validate tria --data data/seed.json`).

(Agent **names** can be anything — they're just registry keys. The folder name must be a valid Python identifier, so we use `lead_qualifier` on disk and `lead-qualifier` as the pretty registry name.)

## Design notes

- No `policies.md` ships with these examples — `overclaw setup` will infer one from the agent code, or you can drop in your own via `--policy`.
- Baselines are intentionally leaky (vague prompts, thin tool docstrings, wrong model tier, unbounded iteration) — but in realistic ways. They aren't straw-manned.
- Seed datasets (`data/seed.json`) are small (5–10 cases). Let`overclaw setup` synthesise more from the policy.
