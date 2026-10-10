# Benchmark evidence storage

The implementation, replay scripts, benchmark summaries and smaller reports are
tracked in Git. The complete MCP receipt is tracked as
`decision-benchmarks-results.json.gz`; decompress it before using the report or
monitoring replay scripts. The original JSON and full compressed BoolQ report
remain local generated artifacts because they exceed the repository’s 500 KiB
added-file limit. Neither local artifact was deleted.

| Artifact relative to tests/evidence         |     Bytes | SHA-256 of file bytes                                              |
| ------------------------------------------- | --------: | ------------------------------------------------------------------ |
| `decision-benchmarks-results.json`          | 1,326,588 | `16a4995b164b4abd8a70ece55bbd199aad8261a4e2d84a28d8d1bf0991683401` |
| `decision-benchmarks-results.json.gz`       |   116,642 | `5a89951621b28c809010acee1298c9cb37a1fc82aaa80682f1273201e349ff30` |
| `decision-benchmarks-reports/boolq.json.gz` | 5,252,726 | `06ddb090e70c58a8c15e8118fff57891ca9358c9f79ec30fddc7efe391b8ba36` |

The original, uncompressed BoolQ report is 70,860,363 bytes with SHA-256
`70dc44b6920a7219c0dcacd775ee96e16e7d1571d352dfde9f87186997b318a0`.
Its evaluation is `58a99159-2626-4af8-a960-457c09112b8e` in local project
`1e3f3e92-b50d-4590-85ed-97921d132d3c`.

To restore the full receipt and download existing reports, run from the repository
with the local Overmind backend and its saved, address-bound account connection:

```sh
gzip -dc tests/evidence/decision-benchmarks-results.json.gz > tests/evidence/decision-benchmarks-results.json
.venv/bin/python tests/evidence/decision_benchmarks_report.py
```

This reads the completed evaluation records and report artifacts; it does not
launch training or repeat provider inference. The report generator verifies the
retained report checksum where supplied and regenerates the compressed reports,
summary JSON and result table. Provider-backed records are required for that
read-only replay. The committed summaries remain inspectable without the backend.
