# Clinical Evidence Q&A

Answers clinical questions with a GRADE-like evidence grade, grounded in
PubMed abstracts and clinical guidelines.

**Stack:** Anthropic SDK + PubMed E-utilities (free, no key) + EXA + Jina Reader.

## Seeded sub-optimalities

- Prompt doesn't require the mandatory disclaimer string.
- No evidence-hierarchy rule — the baseline happily weights a case report
  equal to an RCT.
- Claude Haiku is too small; OverClaw should prefer Sonnet for this task
  (model-selection *upgrade* candidate).
- Agent will cite PMIDs without fetching their abstracts first — a
  hallucination risk that OverClaw can close by enforcing
  `pubmed_fetch_abstract` before citation.
- No disclaimer, no refusal path for individual-patient questions.

## Register

```bash
overclaw agent register clinical-evidence new_examples.clinical_evidence.agent:run
overclaw setup clinical-evidence
overclaw optimize clinical-evidence
```
