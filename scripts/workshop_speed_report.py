import json
from collections import defaultdict
from pathlib import Path


def preparation(run):
    return next(a["seconds"] for a in run["actions"] if a["name"] == "prepare_workshop_environment")


def price(run):
    return "unknown" if run["reported_cost_usd"] is None else f"${run['reported_cost_usd']:.4f}"


ROOT = Path("output/workshop-speed")
rows = [
    json.loads(line)
    for line in (ROOT / "transfers.log").read_text().splitlines()
    if line.startswith("{")
]
comparisons = defaultdict(list)
for row in rows:
    if row["event"] == "comparison":
        comparisons[row["case"]].append(row)
summary = []
for case, files in comparisons.items():
    if len(files) != 2:
        continue
    before = sum(item["before"]["seconds"] for item in files)
    after = sum(item["after"]["seconds"] for item in files)
    summary.append(
        {
            "case": case,
            "before_seconds": before,
            "after_seconds": after,
            "saved_percent": 100 * (1 - after / before),
            "verified_retry_seconds": sum(item["retry"]["seconds"] for item in files),
            "before_wire_bytes": sum(
                item["before"]["uploaded_bytes"] + item["before"]["downloaded_bytes"]
                for item in files
            ),
            "after_wire_bytes": sum(item["after"]["uploaded_bytes"] for item in files),
            "sha256_equal": all(
                item["before"]["sha256"] == item["after"]["sha256"] for item in files
            ),
        }
    )
(ROOT / "transfer-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
lines = [
    "# Workshop latency investigation",
    "",
    "The previous 200,000-record Orca task took 1,196.5 seconds: preparation 906.2 seconds, model calls 75.7 seconds and publication 139.2 seconds. The verified briefing itself took 2.1 seconds. Source movement was the largest measured phase.",
    "",
    "## Controlled source-staging comparison",
    "",
    "The same retained source Parquet and original JSONL are transferred through the former and current implementations, serially, into isolated temporary paths in the existing private Daytona workspace. Order alternates by file/case. Every byte is verified by SHA-256 and size; no model calls or transformations are involved. Temporary benchmark files are removed. These are single paired observations, not latency percentiles; network conditions vary.",
    "",
    "| Records | Former staging | Current staging | Reduction | Verified retry | Former/current file bytes over network |",
    "|---:|---:|---:|---:|---:|---:|",
]
for item in summary:
    lines.append(
        f"| {item['case'].split('-')[-1]} | {item['before_seconds']:.2f}s | {item['after_seconds']:.2f}s | {item['saved_percent']:.1f}% | {item['verified_retry_seconds']:.2f}s | {item['before_wire_bytes'] / 1e6:.2f} / {item['after_wire_bytes'] / 1e6:.2f} MB |"
    )
lines += [
    "",
    "The current path compresses once, uploads once, and expands/hashes inside Daytona before an atomic read-only install. A retry hashes the existing destination and reuses only an exact match. Size limits bound decompression. Corrupt, oversized and truncated transfers preserve the previous destination. The model receives the same source bytes and contracts; no validation or reasoning step was removed.",
    "",
    "## Fresh native runs",
    "",
    "| Case | Prior task / new task | Prior preparation / new preparation | Prior model cost / new model cost | Audit |",
    "|---|---:|---:|---:|---|",
]
new_runs = []
for family in ["covertype", "orca"]:
    key = f"{family}-50000"
    directory = ROOT / "runs" / (key + "-speed-20261109")
    corrected = ROOT / "runs" / (key + "-normalization-20261109")
    if (corrected / "metrics.json").exists():
        directory = corrected
    if not (directory / "metrics.json").exists():
        continue
    before = json.loads((Path("output/workshop-scale") / key / "metrics.json").read_text())
    after = json.loads((directory / "metrics.json").read_text())
    audit = (
        json.loads((directory / "audit.json").read_text())
        if (directory / "audit.json").exists()
        else None
    )
    state = "not passed"
    if audit and audit.get("passed"):
        state = "pass; partitions verified" if audit["splits"] else "pass; no holdouts"
    if family == "orca":
        comparison = directory / "prior-overlap-audit.json"
        if not comparison.exists():
            state += "; prior-definition overlap pending"
        elif json.loads(comparison.read_text())["cross_role_edges"]:
            state = "not qualified: prior-definition overlap"
    lines.append(
        f"| {key} | {before['elapsed_seconds']:.1f}s / {after['elapsed_seconds']:.1f}s | {preparation(before):.1f}s / {preparation(after):.1f}s | {price(before)} / {price(after)} | {state} |"
    )
    new_runs.append({"case": key, "before": before, "metrics": after, "audit": audit})
lines += [
    "",
    "Native runs use fresh datasets, the same raw source bytes and minimal intent, and the same OpenRouter model/standard route. This change does not replace the Daytona image. The prior 50,000-record measurements used runtime c2ba39f456fbe620; these runs use the already-qualified 041df2d9674ed369 image with the Unicode JSONL correction from the preceding scale trial. Full source, input, target, lineage and consumer checks are rerun. Partition checks apply where produced; absent holdouts remain unmeasured. Agent plans can vary, so overall task differences are not attributed entirely to transport. Initial local uploads and external audits are excluded from task time.",
    "",
    "## Quality regression caught during comparison",
    "",
    "The initial optimized Orca candidate completed in 271.9 seconds for $0.1063847 and passed source fidelity, consumer, lineage and its declared grouping checks. It was not accepted as a quality-preserving comparison: its different numeric-token normalization let 13 pairs from the prior run's 73,495 numeric-template matches cross split roles. Its larger edge count did not establish coverage of the previous rule.",
    "",
    "The native reference now requires applicable normalization-view sensitivity checks, reconciliation of plausible matching relationships, preservation of accepted relationships during optimization, and independent checks against actual role assignments. This is general matching guidance, not an Orca-specific transformation or threshold. The corrective run retains the same minimal user intent and is separately audited against the complete previous matching definition. The initial candidate and its failed regression evidence remain retained.",
    "",
    "## Whole-task phase comparison",
    "",
    "| Case | Phase | Prior | Current |",
    "|---|---|---:|---:|",
]
for item in new_runs:
    for phase, field in [
        ("Preparation", "prepare_workshop_environment"),
        ("Model requests", None),
        ("Publication", "publish_workshop"),
    ]:
        values = [
            next(a["seconds"] for a in run["actions"] if a["name"] == field)
            if field
            else run["model_seconds"]
            for run in (item["before"], item["metrics"])
        ]
        lines.append(f"| {item['case']} | {phase} | {values[0]:.2f}s | {values[1]:.2f}s |")
    remainder = [
        run["elapsed_seconds"] - sum(a["seconds"] for a in run["actions"])
        for run in (item["before"], item["metrics"])
    ]
    lines.append(
        f"| {item['case']} | Local tools, clean replay and coordination | {remainder[0]:.2f}s | {remainder[1]:.2f}s |"
    )
lines += [
    "",
    "The last row is the measured elapsed-time remainder outside retained preparation, model and publication actions; it is not attributed to a single function. Per-tool execution receipts are retained alongside each run.",
    "",
    "## Publication breakdown",
    "",
    "| Case | Compress | Download | Verify/unpack | Replay hashes | Pipeline | Partition |",
    "|---|---:|---:|---:|---:|---:|---:|",
]
for item in new_runs:
    timing = (item["metrics"].get("publication") or {}).get("timing") or {}
    transport = timing.get("transfer", {})
    values = [
        transport.get("compress_seconds"),
        transport.get("download_seconds"),
        transport.get("verify_and_unpack_seconds"),
        timing.get("verify_replay_seconds"),
        timing.get("pipeline_seconds"),
        timing.get("partition_seconds"),
    ]
    cells = ["not run" if value is None else f"{value:.2f}s" for value in values]
    lines.append("| " + " | ".join([item["case"], *cells]) + " |")
lines += [
    "",
    "Publication timing is recorded on the retained pipeline run, and source transfer timing is retained outside the model context. Existing lineage, checksum, consumer and contamination checks remain in place. These runs establish preservation and compatibility, not predictive performance, mathematical truth or production concurrency capacity.",
    "",
    "## Findings and remaining work",
    "",
    "The controlled transfer comparison isolates the implemented saving: 75% fewer file bytes cross the network on these sources, with complete size/hash checks and safe retry reuse. At 200,000 records the paired staging observation improves from 462.4 to 86.1 seconds. The new native Orca preparation takes 33.4 seconds versus 193.8 seconds previously. Network variability remains visible between observations; these are not service-level latency guarantees.",
    "",
    "The corrected native Orca run takes 289.4 seconds versus 524.3 seconds, a 44.8% reduction. Its all-record independent overlap verification takes 52.2 seconds across three views, versus 133.5 seconds across two views previously, using indexed candidates while preserving exact boundary checks. The external comparison reproduces all 27,947 previous lexical edges and 73,495 previous numeric-template edges and finds zero crossing the corrected split. All 50,000 original questions and answers remain verbatim. Covertype improves modestly, 181.8 to 174.1 seconds; its source is much smaller in bytes. Every one of its 2.7 million measurement values and observed targets is independently checked.",
    "",
    "Model requests remain approximately 71 seconds for Covertype and 76 seconds for corrected Orca. Corrected Orca also spends 145 seconds in local tools, clean replay and coordination, including 32 seconds of matching exploration and 52 seconds of independent overlap verification. Publication takes 35 seconds: about 14 seconds transferring outputs and 17 seconds constructing/validating the pipeline and partitions. Covertype publication spends 31 seconds in pipeline/partition processing and 7 seconds downloading artifacts. The next investigation should profile those retained-validation passes and turn exact indexed matching into a consistently reused primitive, with equivalence checks; reducing coverage or buying faster model serving is not supported by these measurements.",
    "",
    "Reported model spend for all three fresh tasks, including the rejected candidate, is $0.3058351. Corrected Orca costs $0.0921278 versus $0.0984478 previously; Covertype costs $0.1073226 versus $0.1067692. These are actual single-run provider charges, not forecasts. The provider, serving route and workspace allocation are unchanged. Existing workspace/storage charges are not allocated per run, and the transfer-only comparisons make no model requests.",
    "",
    "Focused tests: 31 passed; both selected work-once journeys passed. Corrupt, oversized and truncated transfers retain the preceding input; verified retries and empty files are covered. Required pre-commit checks pass. Local API and Console HTTP checks return 200, configured container health checks pass, and the private always-running native workspace releases ownership after completion. The Console was not visually inspected.",
    "",
    "Sampling at 20-second intervals observed at most 2.16 GiB anonymous workspace memory and no increase in memory/OOM events. Accumulated retained trials leave only 0.29 GiB free on the existing 10 GiB disk; retention/capacity work is required before further large runs. No retained source or user artifact was deleted. Full native reruns here cover 50,000-record decision/SFT tasks; 100,000/200,000-record measurements in this investigation cover source staging only. Semantic paraphrase independence and the truth of synthetic answers remain unestablished.",
    "",
    "Evidence: output/workshop-speed/transfers.log, transfer-summary.json and per-run metrics/audit/package receipts. No model training was launched.",
    "",
]
Path("scripts/workshop-speed-trial.md").write_text("\n".join(lines))
print(json.dumps(summary, indent=2))
