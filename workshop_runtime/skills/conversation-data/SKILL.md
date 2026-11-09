---
name: conversation-data
description: Prepare and audit instruction, conversation and tool-use data for SFT or model evaluation.
---

Identify the example unit and intended assistant behaviour from the intent and actual source values. Inspect representative and exceptional records, context availability, observed targets, tool-call/result pairing, language, missingness and repetition. The briefing establishes observed structure, not semantic quality. Preserve source instruction, context and response strings exactly unless the intent calls for changes. Do not invent responses, reasoning, evidence or tool results.

Use the native consumer contracts below. Keep source identities and auditable mappings. Group repeated instructions/context, conversations and related entities before partitioning; assess near duplicates as the data warrants. Distinguish a genuinely empty context from missing instructions or answers. Document limitations of synthetic or unverified responses; valid chat syntax does not establish factual truth.

native.json defines packaging, replay and publication; taxonomy.json has additional platform context if needed. Before packaging, use workshop_runtime.data.stage('data/input.parquet', 'preflight.jsonl') to test the exact replay input shape. Exploration can see \_overmind_provenance but clean replay cannot. If audit fields need original file or extraction identities, retain an explicit source_row-to-metadata mapping in the package based on inspected provenance. Never author \_overmind_provenance or silently substitute missing audit values.

After replay, compare every output message to its source fields, check roles and tool pairing with the consumer validators, and verify complete/disjoint splits and group separation. Publish the retained replay only after these independent checks. Tokenizer templates and loss masks belong to downstream training. Finish with roles and material limitations, without claiming that formatting alone makes responses trustworthy.
