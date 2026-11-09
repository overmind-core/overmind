---
name: decision-data
description: Prepare and audit decision training or evaluation from observed labels, scores or distributions.
---

Establish the example unit, prediction moment, target semantics and available features from the intent and actual source evidence. The measured briefing establishes shape, not meaning. Inspect representative and exceptional records; resolve label aliases and exclude outcome-derived inputs. Keep uncertainty explicit. Never turn an unknown target into a guessed label.

Preserve observed distributions, ties, weights and identities. Record one consistent question and criteria map. Assess nulls, entity grouping and label-blind near matches before fixing split groups; exact duplicate checks alone are insufficient. Test sensitivity to plausible matching definitions and independently measure overlap in the final partitions. Fit learned preprocessing on training only. Retain all rows or explain exclusions with identities and evidence.

Use the exact native contracts below. Keep detailed evidence and assumptions in notes.md with the code, rather than repeating all source rows. native.json defines packaging, replay and publication; taxonomy.json has additional platform context if needed. Before packaging, use workshop_runtime.data.stage('data/input.parquet', 'preflight.jsonl') to test the same input shape as clean replay. This intentionally excludes platform-managed metadata; retain any needed metadata as ordinary source-bound audit fields explicitly in the package.

After replay, independently compare actual targets and states to the original records, validate the training/evaluation readers, and verify complete, disjoint, contamination-aware splits. Publish the retained replay only after these checks. A successful receipt proves fidelity, not predictive quality. Finish with roles, assumptions and remaining limitations; never claim model performance without training and evaluation.
