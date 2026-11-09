import argparse
import csv
import hashlib
import json
import os
import random
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq
import requests
from daytona import Daytona, DaytonaConfig
from dotenv import load_dotenv

from scripts.workshop_benchmark import PROJECT, Platform, cli, save

ROOT = Path("output/workshop-scale")
SIZES = (1000, 10000, 50000, 100000, 200000)
SOURCES = {
    "covertype": {
        "repo": "mstz/covertype",
        "revision": "72ae206f21f416f3e7981183537e9ab071aff006",
        "file": "covtype.data",
        "rows": 581012,
        "licence": "CC BY 4.0 (UCI original)",
        "objective": "I want to train a decision model to predict forest cover type from these measurements.",
    },
    "orca": {
        "repo": "microsoft/orca-math-word-problems-200k",
        "revision": "29255d1770cc4eac66e5e7fa378cba542c026350",
        "file": "data/train-00000-of-00001.parquet",
        "rows": 200035,
        "licence": "MIT",
        "objective": "I want to fine-tune an assistant to answer maths questions.",
    },
}
COLUMNS = [
    "Elevation",
    "Aspect",
    "Slope",
    "Horizontal_Distance_To_Hydrology",
    "Vertical_Distance_To_Hydrology",
    "Horizontal_Distance_To_Roadways",
    "Hillshade_9am",
    "Hillshade_Noon",
    "Hillshade_3pm",
    "Horizontal_Distance_To_Fire_Points",
    *[f"Wilderness_Area_{i}" for i in range(1, 5)],
    *[f"Soil_Type_{i}" for i in range(1, 41)],
    "Cover_Type",
]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def prepare():
    for family, source in SOURCES.items():
        directory = ROOT / "sources" / family
        directory.mkdir(parents=True, exist_ok=True)
        raw = directory / Path(source["file"]).name
        url = f"https://huggingface.co/datasets/{source['repo']}/resolve/{source['revision']}/{source['file']}"
        if not raw.exists():
            temporary = raw.with_suffix(".download")
            with requests.get(url, stream=True, timeout=(30, 120)) as response:
                response.raise_for_status()
                with temporary.open("wb") as target:
                    for block in response.iter_content(1024 * 1024):
                        target.write(block)
            temporary.replace(raw)
        if family == "covertype":
            assert digest(raw) == "a1825eb1a33610ad72b10ea943f20aed7951f503553ab0b872348ceb116f62c1"
            with raw.open() as handle:
                reader = csv.reader(handle)
                header = next(reader)
                # The pinned mirror has a 52-column header over the original 55-column UCI rows.
                assert len(header) == 52 and header[0] == "elevation"
                records = list(reader)
            assert all(len(row) == len(COLUMNS) for row in records)
        else:
            records = pq.read_table(raw).to_pylist()
        assert len(records) == source["rows"]
        order = list(range(len(records)))
        random.Random(20261108).shuffle(order)
        save(
            directory / "provenance.json",
            {
                **source,
                "url": url,
                "sha256": digest(raw),
                "bytes": raw.stat().st_size,
                "selection": "Nested prefixes of Python Random(20261108) shuffled source ordinals; no replacement or duplication.",
                "transport_change": "Replaced the malformed header with UCI's documented 55-column schema; all raw data bytes match the UCI original after removing that header."
                if family == "covertype"
                else "Lossless Parquet to JSONL; source question and answer strings unchanged.",
            },
        )
        for size in SIZES:
            key = f"{family}-{size}"
            case_dir = ROOT / key
            case_dir.mkdir(exist_ok=True)
            selected = order[:size]
            target = case_dir / (family + (".csv" if family == "covertype" else ".jsonl"))
            with target.open("w", newline="") as handle:
                if family == "covertype":
                    writer = csv.writer(handle, lineterminator="\n")
                    writer.writerow(COLUMNS)
                    writer.writerows(records[i] for i in selected)
                else:
                    for i in selected:
                        handle.write(json.dumps(records[i], ensure_ascii=False) + "\n")
            save(case_dir / "source-ordinals.json", selected)
            save(
                case_dir / "case.json",
                {
                    **source,
                    "key": key,
                    "family": family,
                    "rows": size,
                    "files": [str(target)],
                    "sha256": digest(target),
                    "bytes": target.stat().st_size,
                    "selection": "Seed 20261108, nested shuffled subset without replacement.",
                },
            )
            print(key, target.stat().st_size, "bytes", flush=True)


def connect():
    platform = Platform()
    # Polling at the server's keep-alive boundary can race an idle connection close.
    platform.http.headers["Connection"] = "close"
    return platform


def run(keys):
    platform = connect()
    for key in keys:
        directory = ROOT / key
        path = directory / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        case = json.loads((directory / "case.json").read_text())
        if not state.get("dataset"):
            started = time.time()
            result = platform.call(
                "start_dataset",
                {
                    "project_id": PROJECT,
                    "name": "Scale trial · " + key,
                    "brief": f"Source: https://huggingface.co/datasets/{case['repo']} at {case['revision']}. Licence: {case['licence']}. {case['selection']}",
                    "intent": "train",
                    "request_key": "scale-source-20261108-" + key,
                },
            )
            state = {"dataset": result["dataset"]["id"], "case": case, "start_epoch": started}
            save(path, state)
        if not state.get("uploaded"):
            started = time.monotonic()
            cli(
                [
                    "dataset",
                    "upload",
                    *case["files"],
                    "--project-id",
                    PROJECT,
                    "--dataset",
                    state["dataset"],
                    "--request-key",
                    "scale-upload-20261108-" + key,
                    "--wait",
                ],
                directory / "upload.log",
            )
            state.update(uploaded=True, upload_seconds=time.monotonic() - started)
            save(path, state)
            print(key, "landed", round(state["upload_seconds"], 2), "seconds", flush=True)
        if not state.get("task"):
            result = platform.call(
                "start_workshop_task",
                {
                    "project_id": PROJECT,
                    "dataset": state["dataset"],
                    "objective": case["objective"],
                    "request_key": "scale-task-20261108-" + key,
                    "max_steps": 0,
                },
            )
            state["task"] = result["task"]["id"]
            save(path, state)
        last = None
        while True:
            try:
                result = platform.call(
                    "inspect_workshop_task", {"project_id": PROJECT, "task": state["task"]}
                )
            except requests.RequestException as exc:
                print(key, "read-only monitor reconnect", type(exc).__name__, flush=True)
                platform.http.close()
                time.sleep(3)
                platform = connect()
                continue
            task = result["task"]
            status = (task["state"], task["step"], task["result"].get("activity"))
            if status != last:
                print(key, status, flush=True)
                last = status
            save(directory / "task.json", result)
            if task["state"] in {"completed", "failed", "blocked", "cancelled"}:
                state["state"] = task["state"]
                state.setdefault("finish_epoch", time.time())
                save(path, state)
                break
            time.sleep(5)
        measurement = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "-e",
                "TRIAL_TASKS=" + state["task"],
                "workshop-agent",
                "python",
                "manage.py",
                "shell",
            ],
            input=Path("scripts/workshop_scale_measure.py").read_text(),
            capture_output=True,
            text=True,
        )
        (directory / "metrics.log").write_text(measurement.stdout + measurement.stderr)
        measurement.check_returncode()
        metrics = json.loads(
            next(line for line in measurement.stdout.splitlines() if line.startswith("{"))
        )
        save(directory / "metrics.json", metrics)
        print(
            key,
            {k: metrics[k] for k in ["state", "elapsed_seconds", "reported_cost_usd"]},
            flush=True,
        )
        if task["state"] != "completed":
            raise RuntimeError(f"{key} {task['state']}; inspect retained evidence before advancing")
        audit = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "-e",
                "SCALE_CASES=" + key + ":" + state["task"],
                "-e",
                "SCALE_MANIFEST=" + json.dumps({key: case}),
                "workshop-agent",
                "python",
                "manage.py",
                "shell",
            ],
            input=Path("scripts/workshop_scale_audit.py")
            .read_text()
            .replace('if __name__ == "__main__":', "if True:"),
            capture_output=True,
            text=True,
        )
        (directory / "audit.log").write_text(audit.stdout + audit.stderr)
        audit.check_returncode()
        report = json.loads(
            next(line for line in audit.stdout.splitlines() if line.startswith("{"))
        )
        save(directory / "audit.json", report)
        print(key, "independent audit passed", report["splits"], flush=True)


def report():
    observations = [
        json.loads(line) for line in (ROOT / "resources.jsonl").read_text().splitlines()
    ]
    results = []
    for size in SIZES:
        for family in SOURCES:
            directory = ROOT / f"{family}-{size}"
            if not (directory / "metrics.json").exists():
                continue
            metrics = json.loads((directory / "metrics.json").read_text())
            state = json.loads((directory / "state.json").read_text())
            audit = (
                json.loads((directory / "audit.json").read_text())
                if (directory / "audit.json").exists()
                else None
            )
            recovery = (
                json.loads((directory / "recovery.json").read_text())
                if (directory / "recovery.json").exists()
                else None
            )
            begin, end = (
                datetime.fromisoformat(metrics[k]).timestamp() for k in ("created_at", "updated_at")
            )
            samples = [r for r in observations if begin <= r["epoch"] <= end and "memory" in r]
            memory = [
                dict(line.split() for line in r["memory"]["memory.stat"].splitlines())
                for r in samples
            ]
            phases = {action["name"]: action["seconds"] for action in metrics["actions"]}
            results.append(
                {
                    "case": directory.name,
                    "rows": size,
                    "input_bytes": state["case"]["bytes"],
                    "task": state["task"],
                    "dataset": state["dataset"],
                    "state": metrics["state"],
                    "upload_seconds": state["upload_seconds"],
                    "task_seconds": metrics["elapsed_seconds"],
                    "upload_plus_task_seconds": state["upload_seconds"]
                    + metrics["elapsed_seconds"],
                    "model_seconds": metrics["model_seconds"],
                    "model_calls": len(metrics["calls"]),
                    "prepare_seconds": phases.get("prepare_workshop_environment"),
                    "publish_seconds": phases.get("publish_workshop"),
                    "briefing_seconds": (metrics.get("briefing_metrics") or {}).get("seconds"),
                    "model_cost_usd": metrics["reported_cost_usd"],
                    "audit": audit,
                    "recovery": recovery,
                    "max_sampled_anonymous_memory_bytes": max(int(m["anon"]) for m in memory)
                    if memory
                    else None,
                    "min_sampled_free_disk_bytes": min(r["disk"]["free"] for r in samples)
                    if samples
                    else None,
                    "resource_sample_count": len(samples),
                    "memory_events_first_last": [
                        samples[i]["memory"]["memory.events"] for i in (0, -1)
                    ]
                    if samples
                    else None,
                    "runtime": metrics["runtime"],
                    "contracts": metrics["runtime_contracts_sha256"],
                }
            )
    save(ROOT / "summary.json", results)
    lines = [
        "# Native Workshop scale trial",
        "",
        "## Method",
        "",
        "Two public Hugging Face corpora, nested shuffled subsets of 1,000, 10,000, 50,000, 100,000 and 200,000 real records. Seed 20261108; no duplicated padding. Exact revisions, source hashes and selected ordinals are retained under output/workshop-scale. Each case starts a fresh dataset and native task in the existing persistent Daytona workspace. Model and serving route remain OpenRouter openai/gpt-6.1-sol, standard pricing, with no agent step/token budget.",
        "",
        "- [Covertype](https://huggingface.co/datasets/mstz/covertype): 54 measurement/indicator features and seven observed cover classes; [UCI original](https://archive.ics.uci.edu/dataset/31/covertype), CC BY 4.0. The pinned mirror's 52-column header is malformed. All 581,012 data rows match the original UCI bytes exactly; only the header was replaced with the documented 55-column schema.",
        "- [Orca Math](https://huggingface.co/datasets/microsoft/orca-math-word-problems-200k): 200,035 synthetic question/answer pairs, MIT. Lossless Parquet-to-JSONL transport; text is unchanged. Formatting these records does not establish mathematical correctness.",
        "",
        "Minimal intents: ‘I want to train a decision model to predict forest cover type from these measurements.’ and ‘I want to fine-tune an assistant to answer maths questions.’",
        "",
        "The independent audit reads every row, verifies original upload bytes and landed values, walks lineage, checks transformed inputs/targets with downstream readers, and, where partitions are produced, proves complete/disjoint membership with no exact-input or declared-group overlap. Training-only output is reported separately; missing holdouts are not scored as zero overlap. It uses SQLite to avoid holding the entire audit population in memory. Semantic correctness, geographic independence and near-duplicate recall remain separate from these mechanical checks.",
        "",
        "## Measurements",
        "",
        "Task time includes preparation, model calls, local execution, clean replay and publication. Upload/landing is measured separately. Source download and the external audit are excluded. A dagger marks a recovered task whose elapsed time includes failed attempts and operator repair; it is not an uninterrupted latency measurement. Model cost is actual provider-reported usage, not an estimate; workspace and storage charges are not allocated per task. Anonymous memory is the maximum observed at 20-second intervals across the workspace, not an exact per-process peak. File cache is excluded. One run per cell measures capacity, not statistically stable latency.",
        "",
        "| Case | Input MB | Upload s | Task s | Model s | Calls | Model USD | Audit | Sampled anon GiB |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for r in results:
        money = f"${r['model_cost_usd']:.4f}" if r["model_cost_usd"] is not None else "unknown"
        memory = (
            f"{r['max_sampled_anonymous_memory_bytes'] / 2**30:.2f}"
            if r["max_sampled_anonymous_memory_bytes"] is not None
            else "unknown"
        )
        model_time = f"{r['model_seconds']:.1f}" if r["model_seconds"] is not None else "unknown"
        elapsed = f"{r['task_seconds']:.1f}" + (" †" if r["recovery"] else "")
        audit_status = r["state"] + ": not passed"
        if r["audit"]:
            audit_status = "pass" if r["audit"]["splits"] else "pass; no holdouts"
        lines.append(
            f"| {r['case']} | {r['input_bytes'] / 1e6:.2f} | {r['upload_seconds']:.1f} | {elapsed} | {model_time} | {r['model_calls']} | {money} | {audit_status} | {memory} |"
        )
    lines += [
        "",
        "| Case | Prepare s | Briefing s (within prepare) | Model s | Publish s |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in results:
        values = [
            "unknown" if r[key] is None else f"{r[key]:.1f}"
            for key in ("prepare_seconds", "briefing_seconds", "model_seconds", "publish_seconds")
        ]
        if r["recovery"]:
            values[-1] += " †"
        lines.append("| " + " | ".join([r["case"], *values]) + " |")
    for r in results:
        recovery = r["recovery"]
        if recovery:
            lines += [
                "",
                f"† {r['case']}: resumed the original task and retained replay; resume-to-completion was {recovery['resume_to_completion_seconds']:.1f} seconds. Whole-task and publication-action durations include the failure and repair interval. The prior failed snapshot is retained under failed-publication-attempt/.",
            ]
    lines += [
        "",
        "## Corrections during the ramp",
        "",
        "The first 200,000-row Covertype publication exhausted its coordinator retries after three backend worker restarts. Daytona had completed transformation, clean replay and independent local checks. The publisher loaded all replay outputs plus decoded records and whole-array JSON hashes into memory. It now downloads to temporary disk, streams output records and incrementally hashes reconstructed input. Checksum, row-count, lineage and consumer checks remain intact. Resume permits the transactionally request-keyed publication while still refusing unknown model acknowledgements. The same retained run then published and passed the full external audit; its restarted backend cgroup peaked at 1.66 GiB with no OOM event. The earlier process exit cause was not retained, so memory pressure is a supported diagnosis rather than a proven kernel OOM event.",
        "",
        "The 100,000-record Orca run exposed two source answers containing U+2028. The runtime used str.splitlines() to read JSONL, incorrectly treating that character inside a JSON string as a record boundary. The agent recovered without changing text or dropping rows, and the external audit passed. Its retries remain included in the recorded time and cost. The shared reader now iterates physical file lines; regression checks cover U+0085, U+2028 and U+2029 with LF and CRLF records, including retained output round trips.",
        "",
        "The first eight runs used runtime c2ba39f456fbe620. The 200,000-record cases use corrected runtime 041df2d9674ed369. The corrected image passed 16 live no-model checks, including Unicode preservation through two replay steps, killed-runner recovery, retained kernel state, clean replay isolation, follow-ups and cancellation. CPU/memory/disk remain 4 CPU / 8 GiB / 10 GiB. This is a capacity ramp with a repaired defect, not an unchanged-release latency comparison.",
        "",
        "The accumulated workspace required a 5.37 GB expanded, 1.25 GB compressed checkpoint. Operator limits now allow 8 GiB expanded and 2 GiB compressed, validate paths and check restore disk headroom. Slow Docker transfers and a read timeout required a host-side chunked transfer with per-chunk, prefix-resume and complete-archive SHA-256 verification. The fresh-workspace disk probe was corrected to create its directory before measuring capacity. A verified remote checkpoint is reused on restore retry. Upgrade time is excluded from task timing; the 200,000-record sources were landed during maintenance, so their upload measurements also reflect that activity.",
        "",
        "## Interpretation and remaining limits",
        "",
        "The agent writes and executes population-level transformations; it does not make a model call per source record. Reported model charges therefore do not grow in proportion to row count. At equal row counts Orca carries roughly nine times the input bytes, plus more expensive text-similarity checks. Rows alone are a poor capacity or latency predictor.",
        "",
        "The 200,000-record Orca run deliberately produced training-only chat records and recommended independently sourced, verified maths evaluation questions. It retained all answer variants and made no correctness or holdout-independence claim. Its bounded 4,980-record lexical diagnostic is not comparable to the earlier population-wide grouping checks. The external audit originally assumed partitions; it now verifies every input, target and lineage record independently of partition presence, and records absent partition checks as unknown rather than zero overlap. Nine runs produced verified train/development/final partitions; the tenth passed exact SFT conversion without holdouts.",
        "",
        "The largest Orca run took 1,196.5 seconds: preparation 906.2 seconds (including a 2.1-second briefing), model requests 75.7 seconds, publication 139.2 seconds. Everything after preparation took 290.3 seconds. Reported model charges across all ten cases total $1.10045, including the recovered Covertype task. Workspace, storage and maintenance charges are not included.",
        "",
        "Source staging currently uploads each file and downloads it again to verify its checksum. This is a remaining transfer bottleneck, separate from briefing generation or model latency; it was observed but not changed in this ramp. Remote checksum verification or a verified compressed staging bundle should be measured next. Persistent source, replay and scratch retention also consumes the fixed 10 GiB disk. These serial trials do not qualify multiple concurrent runs, arbitrary document parsing, unlimited retention, predictive accuracy or the truth of synthetic answers.",
        "",
        "Evidence: output/workshop-scale/summary.json, per-case state/task/metrics/audit files, immutable replay/publication receipts and resources.jsonl. No model training was launched.",
        "",
    ]
    Path("scripts/workshop-scale-trial.md").write_text("\n".join(lines))
    print(
        json.dumps(
            [
                {k: r[k] for k in ("case", "task_seconds", "model_cost_usd", "state")}
                for r in results
            ],
            indent=2,
        )
    )


def collect(keys, sandbox_id):
    platform = connect()
    load_dotenv(Path.home() / ".config/overmind/workshop.env")
    daytona = Daytona(
        DaytonaConfig(
            api_key=os.environ["DAYTONA_API_KEY"],
            api_url="https://app.daytona.io/api",
            otel_enabled=False,
        )
    )
    sandbox = daytona.get(sandbox_id)
    for key in keys:
        directory = ROOT / key
        state = json.loads((directory / "state.json").read_text())
        metrics = json.loads((directory / "metrics.json").read_text())
        if metrics.get("publication"):
            pipeline = platform.call(
                "inspect_dataset_workbench",
                {
                    "project_id": PROJECT,
                    "pipeline": metrics["publication"]["pipeline"],
                },
            )
            save(directory / "pipeline.json", pipeline)
            if not (directory / "recipe.zip").exists():
                cli(
                    [
                        "dataset",
                        "pipeline-download",
                        pipeline["pipeline"]["package"],
                        "--project-id",
                        PROJECT,
                        "--output",
                        str(directory / "recipe.zip"),
                    ],
                    directory / "recipe.log",
                )
        code = f"""
from pathlib import Path
import json,hashlib
root=Path('/home/daytona/overmind/{state["task"]}')
executions={{hashlib.sha256(r['code'].encode()).hexdigest():r for p in (root/'.session/runs').glob('*.json') for r in [json.loads(p.read_text())]}}
out=[]
for path in sorted((root/'work/receipts').glob('*.json')):
 r=json.loads(path.read_text()); args=r['arguments']; execution=executions.get(hashlib.sha256(args.get('code','').encode()).hexdigest(),{{}})
 out.append({{'turn':r['turn'],'tool':r['tool'],'summary':args.get('summary'),
 'started_at':execution.get('started_at'),'finished_at':execution.get('finished_at'),
 'seconds':execution['finished_at']-execution['started_at'] if 'finished_at' in execution and 'started_at' in execution else None,
 'error':r['result'].get('error'),'timed_out':execution.get('timed_out')}})
print(json.dumps({{'executions':out,'notes':{{p.name:p.read_text() for p in (root/'work').iterdir() if p.is_file() and p.suffix in ('.md','.json') and p.name in ('notes.md','verification.json','overlap_report.json','neighbor_report.json','overlap_summary.json')}}}}))
"""
        response = sandbox.process.exec("python -c " + shlex.quote(code), timeout=30)
        if response.exit_code:
            raise RuntimeError(response.result)
        evidence = json.loads(response.result)
        save(directory / "native-executions.json", evidence["executions"])
        for name, value in evidence["notes"].items():
            (directory / name).write_text(value)
        print(
            key,
            [
                (r["summary"], r["seconds"])
                for r in evidence["executions"]
                if r["seconds"] is not None
            ],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "run", "report", "collect"])
    parser.add_argument("cases", nargs="*")
    parser.add_argument("--sandbox", help="Workspace containing the retained task files")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "run":
        run(args.cases)
    elif args.action == "report":
        report()
    else:
        if not args.sandbox:
            parser.error("collect requires --sandbox")
        collect(args.cases, args.sandbox)
