import argparse
import json
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).parent
PROJECT = "1e3f3e92-b50d-4590-85ed-97921d132d3c"
FINANCE = "e18b29b5-915d-45a7-80cd-77ffe6559205"
ORIGINS = {
    "mixed": "088b0557-00f3-4a28-8528-762104c2b454",
    "titanic": "6daa3911-5707-4ba7-9913-52166d63050f",
}
CASES = {
    "mixed": (
        PROJECT,
        "f31614eb-2dfa-4a05-a34d-0cc332e30323",
        "e882b605-9f95-47f6-9b20-0267400e9658",
        15000,
    ),
    "titanic": (
        PROJECT,
        "034f3f2e-2c0e-4053-910c-7ad9327de5f4",
        "e004ec33-2b62-4a98-8b08-e1230090018f",
        891,
    ),
    "matching": (
        FINANCE,
        "1e6be2d8-1c31-48e3-b30e-c7ca400710dc",
        "369b56c9-3f77-48a6-8c5c-5aa7f217b602",
        10000,
    ),
}


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f"{args[:3]} exited {result.returncode}: {result.stderr[-2000:]}")
    return json.loads(result.stdout)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def verify_rows(case, before, after):
    originals = {row["source_row"]: row for row in before}
    assert len(after) == len(before) == len(originals)
    assert len({row["source_row"] for row in after}) == len(originals)
    counts = Counter()
    entities = {}
    for row in after:
        original = originals[row["source_row"]]
        assert all(
            row[key] == value for key, value in original.items() if key != "_overmind_provenance"
        )
        assert row["_overmind_provenance"]["parents"]
        d = row["decision"]
        assert "messages" not in row
        if case == "mixed":
            target = original["target"]
            options = original["options"]
            if original["kind"] == "noul":
                options, target = ["No", "Yes"], [1 - target[0], target[0]]
            assert d == {
                "state": original["state"],
                "question": original["question"],
                "kind": original["kind"],
                "options": options,
                "target_probabilities": target,
            }
            assert row["review_flags"] == ["target_interpretation_unverified"]
            counts["soft_targets"] += max(target) < 1
            counts["tied_maxima"] += target.count(max(target)) > 1
            counts["blank_states"] += original["state"] == ""
            counts[d["kind"]] += 1
        elif case == "titanic":
            expected = {
                key: original[key]
                for key in ("Pclass", "Sex", "Age", "SibSp", "Parch", "Fare", "Cabin", "Embarked")
            }
            assert json.loads(d["state"]) == expected
            assert d["target_probabilities"] == [1 - original["Survived"], original["Survived"]]
            assert d["options"] == ["No", "Yes"] and d["target_semantics"] == "categorical_gold"
            counts["missing_feature_rows"] += bool(row["review_flags"])
        else:
            assert d["options"] == ["negative", "positive"]
            assert d["target_probabilities"] == (
                [1, 0] if original["judgement"] == "negative" else [0, 1]
            )
            assert json.loads(d["state"]) == {
                side: {key: original[side][key] for key in ("caption", "schema", "properties")}
                for side in ("left", "right")
            }
            for side in ("left", "right"):
                for entity in [original[side]["id"], *original[side].get("referents", [])]:
                    assert entities.setdefault(entity, row["entity_group"]) == row["entity_group"]
            counts[original["judgement"]] += 1
            counts["review_flagged_rows"] += bool(row["review_flags"])
    return {"verified_rows": len(after), **counts}


async def main(output, suffix, selected_cases=None):
    directory = Path(tempfile.mkdtemp(prefix="decision-workshop-"))
    transport = command("/usr/local/bin/codex", "mcp", "get", "overmind", "--json")["transport"]
    assert transport["url"] == "http://localhost:8000/api/mcp/"
    evidence = {
        "endpoint": transport["url"],
        "suffix": suffix,
        "artifacts": str(directory),
        "cases": {},
    }

    def save():
        output.write_text(json.dumps(evidence, indent=2) + "\n")

    async with (
        httpx.AsyncClient(headers=transport.get("http_headers", {}), timeout=180) as client,
        streamable_http_client(transport["url"], http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        projects = (await session.call_tool("list_projects", {})).structuredContent
        assert projects["connection"]["mcp_url"] == transport["url"]
        evidence["catalog"] = projects["catalog_sha256"]
        upload = json.loads(
            (await session.read_resource(f"overmind://dataset-upload?project_id={PROJECT}"))
            .contents[0]
            .text
        )
        evidence["upload_contract"] = upload

        async def call(project, tool, **args):
            result = await session.call_tool(tool, {"project_id": project, **args})
            assert not result.isError, (tool, result.structuredContent)
            return result.structuredContent

        async def wait(project, kind, identifier, *, expected="completed"):
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                job = await call(project, "get_job", kind=kind, id=identifier)
                if job["status"] in {"completed", "ready", "failed", "cancelled"}:
                    assert job["status"] == expected, job
                    return job
                await anyio.sleep(job.get("poll_after_seconds") or 10)
            raise TimeoutError(identifier)

        async def export(dataset, cell, name):
            path = directory / (name + ".jsonl")
            receipt = await anyio.to_thread.run_sync(
                lambda: command(
                    "overmind",
                    "dataset",
                    "export",
                    dataset,
                    "--cell",
                    cell,
                    "--output",
                    str(path),
                    "--json",
                )
            )
            return records(path), receipt

        try:
            for case, (project, source, parent, count) in CASES.items():
                if selected_cases and case not in selected_cases:
                    continue
                result = evidence["cases"].setdefault(case, {})
                parent_before = await call(project, "inspect_dataset", dataset=parent, cell_limit=1)
                derived = await call(
                    project,
                    "derive_dataset",
                    name=f"Decision Workshop acceptance · {'mixed source' if case == 'mixed' else 'Titanic' if case == 'titanic' else 'entity matching'}",
                    source_cell=source,
                    request_key=f"{suffix}-{case}",
                )
                job = await wait(project, "data_exploration", derived["workflow"]["id"])
                selected = job["progress"]["report"]["output"]
                dataset = selected["dataset_id"]
                result.update(project=project, source=source, dataset=dataset, selected=selected)
                before, result["source_export"] = await export(parent, source, case + "-source")
                assert len(before) == count
                if case == "matching":
                    pipeline = "4b3ad974-0c1c-4868-a962-753811d4bc81"
                    parameters = {"seed": 42, "sample_groups": 100000}
                    result["recipe_reuse"] = True
                else:
                    package_dir = ROOT / (
                        "decision_workshop_pipeline"
                        if case == "mixed"
                        else "decision_titanic_pipeline"
                    )
                    package = await anyio.to_thread.run_sync(
                        lambda package_dir=package_dir, project=project: command(
                            "overmind",
                            "dataset",
                            "pipeline-upload",
                            str(package_dir),
                            "--project-id",
                            project,
                            "--json",
                        )
                    )
                    saved = await call(
                        project,
                        "save_dataset_pipeline",
                        name=f"Decision Workshop acceptance · {case}",
                        package=package["id"],
                        request_key=f"{suffix}-{case}-package",
                        derived_from=ORIGINS[case],
                    )
                    pipeline = saved["pipeline"]["id"]
                    parameters = {"required_semantics": ""} if case == "mixed" else {}
                    result["package"] = {k: package[k] for k in ("id", "sha256")}
                result["pipeline"] = pipeline
                validation = await call(
                    project,
                    "validate_dataset_pipeline",
                    pipeline=pipeline,
                    source_cell=selected["id"],
                    source_fingerprint=selected["fingerprint"],
                )
                assert validation["validation"]["valid"], validation
                result["runs"] = []
                for mode in ("preview", "publish"):
                    args = dict(
                        dataset=dataset,
                        pipeline=pipeline,
                        source_cell=selected["id"],
                        source_fingerprint=selected["fingerprint"],
                        parameters=parameters,
                        request_key=f"{suffix}-{case}-{mode}",
                        mode=mode,
                        preview_rows=50,
                    )
                    started = await call(project, "run_dataset_pipeline", **args)
                    run = await wait(project, "dataset_pipeline", started["run"]["id"])
                    details = run["details"]
                    assert details["execution"] == "isolated_container"
                    assert all(
                        step["exit_code"] == 0
                        and step["output_rows"] == (50 if mode == "preview" else count)
                        for step in details["result"]["steps"]
                    )
                    assert (await call(project, "run_dataset_pipeline", **args))["run"][
                        "id"
                    ] == started["run"]["id"]
                    result["runs"].append({"id": run["id"], "mode": mode, "details": details})
                    print(
                        json.dumps({"case": case, "stage": mode, "status": "completed"}), flush=True
                    )
                    save()
                cell = details["output_cell"]
                result["output_cell"] = cell
                after, result["output_export"] = await export(dataset, cell, case + "-output")
                result["verification"] = verify_rows(case, before, after)
                readiness = await call(
                    project,
                    "check_finetune_readiness",
                    dataset=dataset,
                    cell=cell,
                    validation_enabled=False,
                    eval_model_before=False,
                    eval_model_after=False,
                )
                result["readiness"] = readiness
                assert (
                    readiness["ready"]
                    and readiness["training_contract"]["inference_contract"] == "decision"
                )
                parent_after = await call(project, "inspect_dataset", dataset=parent, cell_limit=1)
                assert parent_before["active"] == parent_after["active"]
                result["parent_active_unchanged"] = True
                if case == "mixed":
                    failed = await call(
                        project,
                        "run_dataset_pipeline",
                        dataset=dataset,
                        pipeline=pipeline,
                        source_cell=selected["id"],
                        source_fingerprint=selected["fingerprint"],
                        parameters={"required_semantics": "posterior"},
                        request_key=f"{suffix}-{case}-unsupported-meaning",
                        mode="publish",
                    )
                    rejected = await wait(
                        project, "dataset_pipeline", failed["run"]["id"], expected="failed"
                    )
                    result["unsupported_meaning"] = rejected
                    assert (await call(project, "inspect_dataset", dataset=dataset))["active"][
                        "id"
                    ] == cell
                else:
                    partition = await call(
                        project,
                        "create_data_partition",
                        name=f"Decision Workshop {case} grouped handoff",
                        source_cell=cell,
                        request_key=f"{suffix}-{case}-partition",
                        recipe={
                            "seed": 42,
                            "fractions": {
                                "train": 0.7,
                                "development": 0.1,
                                "calibration": 0.1,
                                "final": 0.1,
                            },
                            "group_by": ["Ticket" if case == "titanic" else "entity_group"],
                        },
                    )
                    result["partition_request"] = partition
                    partition_job = await wait(
                        project, "data_partition", partition["workflow"]["id"]
                    )
                    result["partition"] = partition_job
                    assigned_groups = {}
                    observed = Counter()
                    members = {m["role"]: m["cell"] for m in partition_job["progress"]["members"]}
                    for role, member in members.items():
                        member_rows, _ = await export(
                            member["dataset_id"], member["id"], f"{case}-{role}"
                        )
                        assert len(member_rows) == member["rows"]
                        for row in member_rows:
                            observed[json.dumps(row["decision"], sort_keys=True)] += 1
                            group = row["Ticket" if case == "titanic" else "entity_group"]
                            assert assigned_groups.setdefault(group, role) == role
                            if role in {"calibration", "final"}:
                                assert row["input"]["decision"] == {
                                    key: row["decision"][key]
                                    for key in ("state", "question", "kind", "options")
                                }
                                assert row["expected_output"] == {
                                    "probabilities": row["decision"]["target_probabilities"]
                                }
                    assert observed == Counter(
                        json.dumps(row["decision"], sort_keys=True) for row in after
                    )
                    result["partition_verified"] = {
                        "rows": sum(observed.values()),
                        "groups": len(assigned_groups),
                        "cross_role_groups": 0,
                        "references_verified": True,
                    }
                    if case == "titanic":
                        preparation = await call(
                            project,
                            "prepare_training_data",
                            dataset=members["train"]["dataset_id"],
                            cell=members["train"]["id"],
                            validation_dataset=members["development"]["dataset_id"],
                            validation_cell=members["development"]["id"],
                            base_model="Qwen/Qwen3-0.6B",
                            context_length=4096,
                            training_type="lora",
                            retry_failed=True,
                        )
                        result["preparation_request"] = preparation
                        save()
                        result["preparation"] = await wait(
                            project, "training_preparation", preparation["id"], expected="ready"
                        )
                save()
                print(
                    json.dumps({"case": case, "verification": result["verification"]}), flush=True
                )
            evidence["state"] = "passed"
        except Exception as exc:
            evidence["state"] = "failed"
            evidence["failure"] = str(exc)
            raise
        finally:
            save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "decision-workshop-verified-results.json"
    )
    parser.add_argument("--request-prefix", default="decision-workshop-20261010-verified")
    parser.add_argument("--case", choices=tuple(CASES), action="append")
    args = parser.parse_args()
    anyio.run(main, args.output, args.request_prefix, args.case)
