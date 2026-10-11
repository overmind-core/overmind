# KYC preparation check audit

Local development only; no training, inference, evaluation or model activation.
Project e18b29b5-915d-45a7-80cd-77ffe6559205; capability KYC Screener.
Fresh dataset 7b876858-e8a2-4d60-8361-ecee802e9b90.
Input: original Downloads/sample_10000.json, explicitly select pairs, SHA256
fe2f701f6034d3adaa586ef747d3c3855ca10fcbac1de97fa3dd7e487058c521.
The local file's metadata does not identify a Hugging Face repository; do not
claim a verified upstream origin or independently verified label semantics.

## Failure cases and evidence to collect before adapting the recipe

- Wrong JSON boundary: compare landed row count and full export with 10,000 pairs.
- The original recipe includes graph identity shortcuts: capture automatic checks
  before adding any agent finding; assess all output rows for those fields.
- Capability scope mismatch: matching labels do not prove full KYC workflow
  coverage; distinguish a generic not-assessed placeholder from specific evidence.
- Row loss: a preview-only fault drops one row despite preserve_rows=true.
- Malformed decision targets: a preview-only fault emits probabilities summing to
  two despite a declared decision_train consumer.
- Semantic corruption: a preview-only fault swaps valid target probabilities;
  verify whether technical checks detect it. Never publish this output.
- Corrected preparation: reuse the existing retained matching recipe's first
  three steps, preserve all observations, maintain connected entity groups,
  remove direct metadata shortcuts from inputs, and preserve supplied labels.
- Verify output against every source row; the test oracle reads original labels,
  rather than trusting transformation-reported checks.
- Record scoped agent findings through MCP, verify inheritance, resolve only the
  demonstrated shortcut treatment, and compare REST/MCP results exactly.
- Preserve unknown external label truth, hard negatives, capability completeness,
  unseen-entity performance and broad leakage risk as unresolved/unassessed.

Commands and observed receipts will be added after execution.
