# Banking77, SST-5 and BoolQ decision benchmarks

Frozen before training on 2026-10-10. Execute through the local Overmind MCP at
`http://localhost:8000/api/mcp/`, project `1e3f3e92-b50d-4590-85ed-97921d132d3c`.

- Train Qwen/Qwen3-0.6B and Qwen/Qwen3.5-0.8B separately on each benchmark.
- Compare the unchanged foundation decision head, trained decision model, and
  `typesafe/jev-1.13`. The foundation baseline is not a prompted chat classifier.
- Use every official test example for Banking77 (3,080) and SST-5 (2,210).
  BoolQ uses all 3,270 labeled validation examples as final evaluation because its
  public dataset has no labeled test split.
- Retain original labels, duplicate observations, exact option order, source
  revision, file checksums, original split and row identities. Banking77 uses the
  complete JSONL mirror, not the deduplicated Parquet mirror.
- Group normalized identical text for Banking77/SST-5 and normalized identical
  passages for BoolQ. Keep final examples unchanged; exclude lower-priority
  overlapping groups from training/development, retaining exclusion evidence in
  an earlier Workshop cell. SST-5's official validation split supplies development.
- Reserve approximately 10% of original training data for development and 10%
  for calibration for Banking77/BoolQ. SST-5 reserves 10% of training for
  calibration and uses its official development split. Group-preserving split
  allocation uses seed 73491. No final labels select recipes or checkpoints.
- Recipe: three complete epochs, effective batch 16, learning rate 0.0002,
  LoRA rank 16 / alpha 32 / dropout 0, training seed 0, context 4096, maximum
  provider runtime 10,800 seconds. No optimizer-step cap. Monitor each epoch and
  select the last checkpoint. Keep the initial development baseline enabled.
- Fit Clef temperature calibration only on the calibration role, separately per
  participant. Report raw accuracy, cross entropy, Brier, coverage and available
  paired uncertainty, plus calibrated probability metrics. One training seed
  does not measure training-run variance. Public benchmark pretraining exposure
  for foundations and Jev is unknown.

Before launch, verify labels/options and split identities across all exported
rows, exact tokenization readiness, frozen source boundaries, cost forecasts,
and the current pinned worker release. Verify checkpoint reload and full final
coverage after training. Preserve failed attempts and repair their causes before
retesting; do not silently replace participant models or benchmark rows.

Concrete failure checks: altered mirror row counts; wrong class-index mapping;
target fields entering model inputs; cross-role duplicate/group leakage; lost
duplicate observations; context truncation; incomplete tokenization; unstable
provider submission; checkpoint reload mismatch; missing/invalid probabilities;
evaluation rows dropped; unreported versus measured costs.

Sources: [Banking77](https://huggingface.co/datasets/PolyAI/banking77),
[Banking77 JSONL mirror](https://huggingface.co/datasets/mteb/banking77),
[SST-5](https://huggingface.co/datasets/SetFit/sst5),
[BoolQ](https://huggingface.co/datasets/google/boolq).
