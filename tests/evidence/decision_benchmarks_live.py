import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).parent
PROJECT = "1e3f3e92-b50d-4590-85ed-97921d132d3c"
MODELS = ["Qwen/Qwen3-0.6B", "Qwen/Qwen3.5-0.8B"]
PREFIX = "decision-benchmarks-20261010"


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError(f"{args[:3]} exited {result.returncode}: {result.stderr[-3000:]}")
    return json.loads(result.stdout)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def monitoring():
    return {
        "mode": "epoch",
        "initial": True,
        "final": True,
        "loss_sample": 1024,
        "train_sample": 64,
        "selection": "last",
        "seed": 73491,
    }


def hyperparameters():
    return {
        "n_epochs": 3,
        "batch_size": 16,
        "context_length": 4096,
        "learning_rate": 0.0002,
        "seed": 0,
        "pre_training_baseline": True,
        "runtime_limit_seconds": 10800,
        "monitoring": monitoring(),
        "training_type": {"type": "Lora", "lora_r": 16, "lora_alpha": 32, "lora_dropout": 0},
    }


async def main(mode, directory, output):
    evidence = json.loads(output.read_text()) if output.exists() else {"cases": {}}
    manifest = json.loads((directory / "manifest.json").read_text())
    transport = command("/usr/local/bin/codex", "mcp", "get", "overmind", "--json")["transport"]
    assert transport["url"] == "http://localhost:8000/api/mcp/"

    def save():
        output.write_text(json.dumps(evidence, indent=2) + "\n")

    async with (
        httpx.AsyncClient(headers=transport.get("http_headers", {}), timeout=600) as client,
        streamable_http_client(transport["url"], http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        projects = (await session.call_tool("list_projects", {})).structuredContent
        assert projects["connection"]["mcp_url"] == transport["url"]
        assert PROJECT in {p["id"] for p in projects["projects"]}
        evidence["endpoint"] = transport["url"]
        evidence["catalog"] = projects["catalog_sha256"]
        evidence["sources"] = manifest
        evidence["upload_contract"] = json.loads(
            (await session.read_resource(f"overmind://dataset-upload?project_id={PROJECT}"))
            .contents[0]
            .text
        )

        async def call(tool, **kwargs):
            result = await session.call_tool(tool, {"project_id": PROJECT, **kwargs})
            if result.isError:
                evidence.setdefault("errors", []).append(
                    {
                        "tool": tool,
                        "arguments": kwargs,
                        "result": result.structuredContent
                        or [c.model_dump() for c in result.content],
                    }
                )
                save()
                raise RuntimeError(f"{tool}: {result.structuredContent or result.content}")
            return result.structuredContent

        async def wait(kind, identifier, expected="completed"):
            deadline = time.monotonic() + 3600
            prior = None
            while time.monotonic() < deadline:
                job = await call("get_job", kind=kind, id=identifier)
                if job["status"] != prior:
                    print(kind, identifier, job["status"], flush=True)
                    prior = job["status"]
                if job["status"] in {
                    "completed",
                    "ready",
                    "prepared",
                    "failed",
                    "cancelled",
                    "preparation_failed",
                }:
                    assert job["status"] == expected, job
                    return job
                await anyio.sleep(job.get("poll_after_seconds") or 10)
            raise TimeoutError(identifier)

        if mode == "data":
            if "package" not in evidence:
                evidence["package"] = await anyio.to_thread.run_sync(
                    lambda: command(
                        "overmind",
                        "dataset",
                        "pipeline-upload",
                        str(ROOT / "decision_benchmarks_pipeline"),
                        "--project-id",
                        PROJECT,
                        "--json",
                    )
                )
                save()
            recipe = await call(
                "save_dataset_pipeline",
                name="Published decision benchmarks with exact final splits",
                package=evidence["package"]["id"],
                derived_from="45368aed-f1f2-43a6-a7ff-b29d9f7a8d55",
                request_key=PREFIX + "-package",
            )
            evidence["pipeline"] = recipe["pipeline"]
            save()
            for benchmark, source in manifest.items():
                local_input = Path(source["source"])
                parameters = directory / "parameters.json"
                parameters.write_text("{}\n")
                for step in ("audit", "eligible", "decisions"):
                    local_output = directory / benchmark / f"{step}.jsonl"
                    subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "decision_benchmarks_pipeline" / f"{step}.py"),
                            str(local_input),
                            str(local_output),
                            str(parameters),
                        ],
                        check=True,
                    )
                    local_input = local_output
                case = evidence["cases"].setdefault(benchmark, {})
                if "dataset" not in case:
                    started = await call(
                        "start_dataset",
                        name=f"{benchmark} · published benchmark source",
                        intent="train",
                        brief="Train typed decision classifiers using original published categorical labels. Freeze the official final split, retain all final observations, group repeated content, keep calibration separate from development, and compare the trained models with unchanged foundation decision heads and Jev 1.13.",
                    )
                    case["start"] = started
                    case["dataset"] = started["dataset"]["id"]
                    save()
                if "upload" not in case:
                    case["upload"] = await anyio.to_thread.run_sync(
                        lambda source=source, case=case, benchmark=benchmark: command(
                            "overmind",
                            "dataset",
                            "upload",
                            source["source"],
                            "--project-id",
                            PROJECT,
                            "--dataset",
                            case["dataset"],
                            "--request-key",
                            f"{PREFIX}-{benchmark}-source",
                            "--json",
                            "--wait",
                        )
                    )
                    selected = (await call("inspect_dataset", dataset=case["dataset"]))["active"]
                    case["source"] = selected
                    save()
                selected = case["source"]
                validation = await call(
                    "validate_dataset_pipeline",
                    pipeline=recipe["pipeline"]["id"],
                    source_cell=selected["id"],
                    source_fingerprint=selected["fingerprint"],
                )
                assert validation["validation"]["valid"], validation
                for run_mode in ("preview", "publish"):
                    if run_mode not in case:
                        started = await call(
                            "run_dataset_pipeline",
                            dataset=case["dataset"],
                            pipeline=recipe["pipeline"]["id"],
                            source_cell=selected["id"],
                            source_fingerprint=selected["fingerprint"],
                            parameters={},
                            request_key=f"{PREFIX}-{benchmark}-{run_mode}",
                            mode=run_mode,
                            preview_rows=100,
                        )
                        case[run_mode] = await wait("dataset_pipeline", started["run"]["id"])
                        save()
                cell = case["publish"]["details"]["output_cell"]
                exported = directory / benchmark / "published.jsonl"
                if not exported.exists():
                    case["export"] = await anyio.to_thread.run_sync(
                        lambda case=case, cell=cell, exported=exported: command(
                            "overmind",
                            "dataset",
                            "export",
                            case["dataset"],
                            "--cell",
                            cell,
                            "--output",
                            str(exported),
                            "--json",
                        )
                    )
                actual = records(exported)
                originals = {r["source_row"]: r for r in records(Path(source["source"]))}
                expected = records(directory / benchmark / "decisions.jsonl")
                assert len(actual) == len(expected)
                expected_by_id = {r["source_row"]: r for r in expected}
                groups, counts = {}, Counter()
                for row in actual:
                    original = originals[row["source_row"]]
                    assert row["record"] == original["record"]
                    assert row["source"] == original["source"]
                    assert row["decision"] == expected_by_id[row["source_row"]]["decision"]
                    assert set(row["input"]["decision"]) == {"state", "question", "kind", "options"}
                    assert (
                        groups.setdefault(row["group_id"], row["benchmark_role"])
                        == row["benchmark_role"]
                    )
                    counts[row["benchmark_role"]] += 1
                assert (
                    counts["final"] == {"banking77": 3080, "sst5": 2210, "boolq": 3270}[benchmark]
                )
                case["verified"] = {
                    "rows": len(actual),
                    "roles": dict(counts),
                    "groups": len(groups),
                    "excluded_training_rows": len(originals) - len(actual),
                }
                if "partition" not in case:
                    request = await call(
                        "create_data_partition",
                        name=f"{benchmark} · frozen official benchmark roles",
                        source_cell=cell,
                        request_key=f"{PREFIX}-{benchmark}-partition",
                        recipe={
                            "seed": 73491,
                            "fractions": {k: v / len(actual) for k, v in counts.items()},
                            "group_by": ["group_id"],
                            "stratify_by": "label",
                            "holdouts": [
                                {"field": "benchmark_role", "role": role, "values": [role]}
                                for role in counts
                            ],
                        },
                    )
                    case["partition"] = await wait("data_partition", request["workflow"]["id"])
                    save()
                case["members"] = {
                    m["role"]: m["cell"] for m in case["partition"]["progress"]["members"]
                }
                assert {k: v["rows"] for k, v in case["members"].items()} == dict(counts)
                save()
                print(benchmark, case["verified"], flush=True)
        elif mode == "verify":
            for benchmark, case in evidence["cases"].items():
                published = {
                    r["source_row"]: r for r in records(directory / benchmark / "published.jsonl")
                }
                observed, groups = set(), {}
                verified = {}
                for role, member in case["members"].items():
                    path = directory / benchmark / f"partition-{role}.jsonl"
                    if not path.exists():
                        await anyio.to_thread.run_sync(
                            lambda member=member, path=path: command(
                                "overmind",
                                "dataset",
                                "export",
                                member["dataset_id"],
                                "--cell",
                                member["id"],
                                "--output",
                                str(path),
                                "--json",
                            )
                        )
                    rows = records(path)
                    assert len(rows) == member["rows"]
                    for row in rows:
                        identity = row["_overmind_provenance"]["file"]["row"]
                        assert identity not in observed
                        observed.add(identity)
                        assert row["record"] == published[identity]["record"]
                        assert row["benchmark_role"] == role
                        assert groups.setdefault(row["group_id"], role) == role
                        decision = row["decision"]
                        assert decision == published[identity]["decision"]
                        assert row["input"] == {
                            "decision": {
                                k: decision[k] for k in ("state", "question", "kind", "options")
                            }
                        }
                        assert row["expected_output"] == {
                            "probabilities": decision["target_probabilities"]
                        }
                    verified[role] = {"rows": len(rows), "labels": len({r["label"] for r in rows})}
                assert observed == set(published)
                case["partition_verified"] = verified
                save()
                print(benchmark, verified, flush=True)
        elif mode == "prepare":
            for benchmark, case in evidence["cases"].items():
                train, development = case["members"]["train"], case["members"]["development"]
                args = {
                    "dataset": train["dataset_id"],
                    "cell": train["id"],
                    "validation_dataset": development["dataset_id"],
                    "validation_cell": development["id"],
                    "monitoring": monitoring(),
                }
                case["readiness"] = await call(
                    "check_finetune_readiness",
                    **args,
                    validation_enabled=True,
                    eval_model_before=False,
                    eval_model_after=False,
                )
                case.setdefault("preparations", {})
                for model in MODELS:
                    result = await call(
                        "prepare_training_data",
                        **args,
                        base_model=model,
                        context_length=4096,
                        training_type="lora",
                    )
                    case["preparations"][model] = result
                    save()
                    print(benchmark, model, json.dumps(result)[:1200], flush=True)
        elif mode == "experiments":
            for benchmark, case in evidence["cases"].items():
                members = case["members"]
                if "comparison" not in case:
                    case["comparison"] = await call(
                        "create_native_evaluation",
                        name=f"{benchmark} · baseline and Jev protocol",
                        request_key=f"{PREFIX}-{benchmark}-protocol",
                        final_cell=members["final"]["id"],
                        calibration_cell=members["calibration"]["id"],
                        baseline="qwen3-base",
                        seed=73491,
                        bootstrap_samples=1000,
                        inference={
                            "batch_size": 32,
                            "concurrency": 8,
                            "context_length": 4096,
                            "padded_tokens": 16384,
                        },
                        participants=[
                            {
                                "key": "qwen3-base",
                                "name": "Qwen3 0.6B unchanged decision head",
                                "kind": "foundation",
                                "model": MODELS[0],
                            },
                            {
                                "key": "qwen35-base",
                                "name": "Qwen3.5 0.8B unchanged decision head",
                                "kind": "foundation",
                                "model": MODELS[1],
                            },
                            {
                                "key": "jev",
                                "name": "Jev 1.13",
                                "kind": "external",
                                "model": "typesafe/jev-1.13",
                            },
                        ],
                    )
                    save()
                print(benchmark, "comparison", json.dumps(case["comparison"])[:250], flush=True)
                comparison = case["comparison"]["workflow"]["id"]
                await call("prepare_native_evaluation", evaluation=comparison)
                case["comparison_preparation"] = await wait(
                    "native_evaluation", comparison, "prepared"
                )
                if "experiment" not in case:
                    case["experiment"] = await call(
                        "create_training_experiment",
                        name=f"{benchmark} · Unsloth decision benchmarks",
                        purpose="Measure three complete training epochs against unchanged decision heads and Jev 1.13 on the complete official held-out benchmark. Fixed recipe and last-checkpoint selection; no final-set tuning.",
                        request_key=f"{PREFIX}-{benchmark}-experiment",
                        evaluation=comparison,
                        variants=[
                            {
                                "name": f"{benchmark} · {model.split('/')[-1]} · three epochs",
                                "base_model": model,
                                "cell": members["train"]["id"],
                                "development_cell": members["development"]["id"],
                                "hyperparameters": hyperparameters(),
                            }
                            for model in MODELS
                        ],
                    )
                    save()
                experiment = case["experiment"]["workflow"]["id"]
                await call("prepare_training_experiment", experiment=experiment)
                case["forecast"] = await wait("training_experiment", experiment, "prepared")
                save()
                print(benchmark, "prepared experiment", experiment, flush=True)
        elif mode == "launch":
            for benchmark, case in evidence["cases"].items():
                assert case.get("partition_verified")
                preparations = {}
                for model, preparation in case["preparations"].items():
                    preparations[model] = await call(
                        "get_job", kind="training_preparation", id=preparation["id"]
                    )
                case["preparation_observations"] = preparations
                save()
                if any(p["status"] != "ready" for p in preparations.values()):
                    print(benchmark, "waiting for verified token artifacts", flush=True)
                    continue
                assert all(not p["progress"]["issues"] for p in preparations.values())
                experiment = case["experiment"]["workflow"]["id"]
                case["launch"] = await call("launch_training_experiment", experiment=experiment)
                save()
                print(benchmark, "launched", experiment, flush=True)
        elif mode == "observe":
            for benchmark, case in evidence["cases"].items():
                if "experiment" in case:
                    case["observation"] = await call(
                        "get_job",
                        kind="training_experiment",
                        id=case["experiment"]["workflow"]["id"],
                    )
                    progress = case["observation"]["progress"]
                    print(benchmark, progress["state"], flush=True)
                    for job in progress["jobs"]:
                        print(
                            job["job_id"],
                            job["name"],
                            job["status"],
                            json.dumps(job["diagnostics"])[:1000],
                            flush=True,
                        )
                    if progress.get("evaluation"):
                        case["evaluation_observation"] = await call(
                            "get_job", kind="native_evaluation", id=progress["evaluation"]
                        )
                        evaluation = case["evaluation_observation"]["progress"]
                        print(
                            "evaluation",
                            evaluation["id"],
                            evaluation["state"],
                            {k: v.get("state") for k, v in evaluation.get("calls", {}).items()},
                            flush=True,
                        )
                for model, prep in case.get("preparations", {}).items():
                    identifier = prep.get("preparation", {}).get("id") or prep.get("job", {}).get(
                        "id"
                    )
                    if identifier:
                        case.setdefault("preparation_observations", {})[model] = await call(
                            "get_job", kind="training_preparation", id=identifier
                        )
                save()
        else:
            raise ValueError(mode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=["data", "verify", "prepare", "experiments", "launch", "observe"]
    )
    parser.add_argument("--directory", type=Path, default=Path("/tmp/decision-benchmarks-20261010"))
    parser.add_argument("--output", type=Path, default=ROOT / "decision-benchmarks-results.json")
    args = parser.parse_args()
    anyio.run(main, args.mode, args.directory, args.output)
