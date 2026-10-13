import asyncio
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from django.db import connection
from typer.testing import CliRunner

import overmind
from overbae.models import Cell, DatasetPipelineRun
from overbae.services.datasets import paths, pipeline_runner, store, workbench
from overbae.services.mcp.catalog import CATALOG
from tests.test_reusable_workshop_journey import call, source, workspace
from tests.workshop_reproducibility_fixture import (
    COLUMNS,
    SCRIPTS,
    THRESHOLD,
    archive,
    fixture_rows,
    manifest,
    oracle,
)

pytestmark = pytest.mark.django_db(transaction=True)

real = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_TEST_IMAGE"),
    reason="Requires a running local Docker daemon and a built Workshop runtime image",
)


def test_oracle_matches_hand_worked_rows():
    rows = fixture_rows()
    expected = oracle(rows, THRESHOLD)
    by_row = {row["source_row"]: row for row in expected["final"]}
    assert by_row[0] == {
        "text": "ticket 0000 refund",
        "priority": "",
        "amount": 0,
        "note": None,
        "source_row": 0,
        "band": "low",
    }
    assert by_row[2] == {
        "text": "ticket 0002 invoice",
        "priority": "high",
        "amount": 74,
        "note": "n2",
        "source_row": 2,
        "band": "mid",
    }
    assert by_row[5]["band"] == "high" and by_row[5]["amount"] == 84
    assert rows[49] == rows[48]
    assert by_row[49] == {**by_row[48], "source_row": 49}
    assert by_row[49]["amount"] == 59 and by_row[49]["band"] == "low"
    assert [row["source_row"] for row in expected["final"]] == [
        row["source_row"] for row in expected["rest"]
    ] + [row["source_row"] for row in expected["keep"]]
    assert len(expected["keep"]) + len(expected["rest"]) == 1000
    assert oracle(rows, 1000)["keep"] == []


def use_runtime(settings):
    settings.WORKSHOP_RUNTIME_IMAGES = [os.environ["WORKSHOP_TEST_IMAGE"]]
    settings.WORKSHOP_DOCKER_SOCKET = os.environ.get(
        "WORKSHOP_DOCKER_SOCKET", "/var/run/docker.sock"
    )
    return settings.WORKSHOP_RUNTIME_IMAGES[0]


def register(client, project, files):
    stream = io.BytesIO(archive(files))
    stream.name = "pipeline.zip"
    response = client.post(
        "/api/dataset-pipeline-packages/",
        {"project": str(project.pk), "file": stream},
        format="multipart",
    )
    assert response.status_code == 201, response.data
    return response.data


def submit(context, dataset, recipe, key, cell, **arguments):
    return call(
        context,
        "run_dataset_pipeline",
        dataset=str(dataset.pk),
        pipeline=recipe["id"],
        source_cell=str(cell.pk),
        source_fingerprint=cell.fingerprint,
        request_key=key,
        **arguments,
    )["run"]


def finish(context, run):
    return call(context, "get_job", kind="dataset_pipeline", id=run["id"])


def execute(context, dataset, recipe, key, cell, **arguments):
    run = submit(context, dataset, recipe, key, cell, **arguments)
    workbench.execute(run["id"], script_executor=pipeline_runner.execute_script)
    return finish(context, run)


def cell_fingerprints(dataset):
    return dict(Cell.objects.filter(dataset=dataset).values_list("pk", "fingerprint"))


def published_steps(job):
    return {step["step_id"]: step for step in job["details"]["result"]["steps"]}


def body(response):
    if not response.streaming:
        return response.content
    if response.is_async:

        async def collect():
            return b"".join([chunk async for chunk in response.streaming_content])

        return asyncio.run(collect())
    return b"".join(response.streaming_content)


def cli_exporter(client, monkeypatch, tmp_path):
    specification = importlib.util.spec_from_file_location(
        "workshop_export_cli", Path(__file__).parents[1] / "overmind/overmind/dataset_cmd.py"
    )
    monkeypatch.setattr(
        overmind,
        "__path__",
        [str(Path(__file__).parents[1] / "overmind/overmind"), *overmind.__path__],
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    class RESTTransport:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, **kwargs):
            response = client.get(urlsplit(url).path, params or {})
            result = requests.Response()
            result.status_code = response.status_code
            result._content = body(response)
            result.raw = io.BytesIO(result.content)
            result.headers.update(response.headers)
            return result

        def close(self):
            pass

    monkeypatch.setattr(module.requests, "Session", RESTTransport)
    runner = CliRunner()

    def export(dataset, cell):
        output = tmp_path / f"{cell}.jsonl"
        invoked = runner.invoke(
            module.dataset_app,
            [
                "export",
                str(dataset.pk),
                "--cell",
                str(cell),
                "--output",
                str(output),
                "--api-key",
                "test-export-key",
                "--api-url",
                "http://testserver",
                "--path",
                str(tmp_path / "absent.toml"),
                "--json",
            ],
        )
        assert invoked.exit_code == 0, invoked.output
        return [json.loads(line) for line in output.read_text().splitlines()]

    return export


def project_rows(rows, keys):
    return [{key: row.get(key) for key in keys} for row in rows]


def assert_matches_oracle(export, dataset, steps, expected):
    exported = {}
    for identity, rows in expected.items():
        exported[identity] = export(dataset, steps[identity]["output_cell"])
        assert project_rows(exported[identity], rows[0].keys() if rows else ["source_row"]) == (
            rows if rows else []
        ), identity
    parents = {
        steps["derive"]["output_cell"]: steps["derive"]["output_fingerprint"],
        steps["label_rest"]["output_cell"]: steps["label_rest"]["output_fingerprint"],
    }
    keep = {row["source_row"] for row in expected["keep"]}
    for row in exported["final"]:
        branch = "derive" if row["source_row"] in keep else "label_rest"
        assert row["_overmind_provenance"]["parents"] == [
            {
                "cell": steps[branch]["output_cell"],
                "fingerprint": parents[steps[branch]["output_cell"]],
                "row": row["source_row"],
            }
        ], row["source_row"]
    return exported


@real
def test_real_multi_stage_run_matches_an_independent_oracle(settings, monkeypatch, tmp_path):
    runtime = use_runtime(settings)
    project, client, context = workspace()
    rows = fixture_rows()
    dataset = source(client, project, "Tickets", rows)
    original = dataset.active_cell
    landed = [
        {key: row.get(key) for key in COLUMNS}
        for row in store.iter_rows(paths.cell_path(dataset.pk, original.pk))
    ]
    assert landed == [{**row, "source_row": index} for index, row in enumerate(rows)]

    files = {"manifest.json": json.dumps(manifest(runtime)), **SCRIPTS}
    package = register(client, project, files)
    for item in package["inventory"]:
        content, offset = "", 0
        while offset is not None:
            page = client.get(
                f"/api/dataset-pipeline-packages/{package['id']}/source/",
                {"file": item["path"], "offset": offset, "limit": 16000},
            )
            assert page.status_code == 200, (item["path"], page.data)
            content += page.data["content"]
            offset = page.data["next_offset"]
        assert content == files[item["path"]]
        assert hashlib.sha256(content.encode()).hexdigest() == item["sha256"]
    assert {item["path"] for item in package["inventory"]} == set(files)
    assert register(client, project, files)["id"] == package["id"]

    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Tickets",
        request_key="tickets",
        package=package["id"],
    )["pipeline"]
    assert recipe["flow"]["terminal_steps"] == ["final"]
    assert recipe["flow"]["unconsumed_steps"] == []

    preview = execute(
        context,
        dataset,
        recipe,
        "preview",
        original,
        mode="preview",
        preview_rows=100,
        parameters={"threshold": THRESHOLD},
    )
    assert preview["status"] == "completed", preview
    assert {cell.pk for cell in Cell.objects.filter(dataset=dataset)} == {original.pk}
    sampled = oracle(rows[:100], THRESHOLD)
    assert {
        identity: step["output_rows"] for identity, step in published_steps(preview).items()
    } == {identity: len(expected) for identity, expected in sampled.items()}

    export = cli_exporter(client, monkeypatch, tmp_path)
    expected = oracle(rows, THRESHOLD)
    first_run = submit(
        context, dataset, recipe, "first", original, parameters={"threshold": THRESHOLD}
    )
    workbench.execute(first_run["id"], script_executor=pipeline_runner.execute_script)
    first = finish(context, first_run)
    assert first["status"] == "completed", first
    first_steps = published_steps(first)
    assert_matches_oracle(export, dataset, first_steps, expected)

    cells = cell_fingerprints(dataset)
    replay = submit(
        context, dataset, recipe, "first", original, parameters={"threshold": THRESHOLD}
    )
    assert replay["id"] == first_run["id"]
    assert cell_fingerprints(dataset) == cells
    conflict = asyncio.run(
        CATALOG.call(
            "run_dataset_pipeline",
            {
                "dataset": str(dataset.pk),
                "pipeline": recipe["id"],
                "source_cell": str(original.pk),
                "source_fingerprint": original.fingerprint,
                "request_key": "first",
                "parameters": {"threshold": THRESHOLD + 1},
            },
            context,
        )
    )
    assert conflict.isError
    assert conflict.structuredContent["error"]["code"] == "request_key_conflict"

    before = cell_fingerprints(dataset)
    queued = submit(
        context, dataset, recipe, "second", original, parameters={"threshold": THRESHOLD}
    )
    pipeline_runner.tick()
    second = finish(context, queued)
    assert second["status"] == "completed", second
    second_steps = published_steps(second)
    # Later steps record this run's own parent cells in their lineage, so only the
    # source-fed step can share a fingerprint across runs; content must match exactly.
    assert second_steps["clean"]["output_fingerprint"] == first_steps["clean"]["output_fingerprint"]
    assert_matches_oracle(export, dataset, second_steps, expected)
    after = cell_fingerprints(dataset)
    assert {key: after[key] for key in before} == before
    assert len(after) == len(before) + len(second_steps)

    before = after
    empty = execute(context, dataset, recipe, "empty", original, parameters={"threshold": 1000})
    assert empty["status"] == "completed", empty
    empty_steps = published_steps(empty)
    assert empty_steps["keep"]["output_rows"] == 0
    assert empty_steps["derive"]["output_rows"] == 0
    expected_empty = oracle(rows, 1000)
    for identity in ("rest", "label_rest", "final"):
        assert (
            project_rows(
                export(dataset, empty_steps[identity]["output_cell"]),
                expected_empty[identity][0].keys(),
            )
            == expected_empty[identity]
        ), identity
    after = cell_fingerprints(dataset)
    assert {key: after[key] for key in before} == before


VALUE = {"value": "integer", "source_row": "integer"}
COPY = (
    "import sys\n"
    "with open(sys.argv[1]) as source, open(sys.argv[2], 'w') as output:\n"
    "    output.write(source.read())\n"
)


def leftover_containers(run_id):
    listed = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=overmind.workshop.run={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return listed.stdout.split()


def run_case(settings, steps, files, *, limits=None, rows=10):
    runtime = use_runtime(settings)
    project, client, context = workspace()
    dataset = source(client, project, "Case", [{"value": index} for index in range(rows)])
    original = dataset.active_cell
    files = {
        "manifest.json": json.dumps(
            {"version": 1, "runtime": runtime, "limits": limits or {"seconds": 60}, "steps": steps}
        ),
        **files,
    }
    package = register(client, project, files)
    recipe = call(
        context, "save_dataset_pipeline", name="Case", request_key="case", package=package["id"]
    )["pipeline"]
    before = cell_fingerprints(dataset)
    run = submit(context, dataset, recipe, "case", original)
    workbench.execute(run["id"], script_executor=pipeline_runner.execute_script)
    job = finish(context, run)
    assert leftover_containers(run["id"]) == []
    return job, dataset, before


def single(entrypoint="step.py", **extra):
    return {
        "id": "step",
        "input": "source",
        "name": "Step",
        "entrypoint": entrypoint,
        "input_schema": {"value": "integer"},
        "output_schema": VALUE,
        **extra,
    }


def assert_nothing_published(job, dataset, before, message):
    assert job["status"] == "failed", job
    assert message in job["details"]["error"], job["details"]["error"]
    assert cell_fingerprints(dataset) == before
    dataset.refresh_from_db()
    assert dataset.state == "idle"


SANDBOX = """import json, os, socket, sys


def refused(action):
    try:
        action()
    except OSError:
        return True
    return False


assert refused(lambda: open("/etc/overmind-escape", "w"))
assert refused(lambda: open("/usr/local/lib/overmind-escape", "w"))
for host_path in ("/var/run/docker.sock", "/code", "/data", "/root/.aws"):
    assert not os.path.exists(host_path), host_path
assert not any(name.endswith(("_KEY", "_TOKEN", "_SECRET")) for name in os.environ)
probe = socket.socket()
probe.settimeout(1)
assert probe.connect_ex(("1.1.1.1", 443)) != 0
with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    output.write(source.read())
"""

PROCESSES = """import subprocess, sys

children = []
try:
    for _ in range(100):
        children.append(subprocess.Popen(["sleep", "30"]))
except OSError:
    pass
for child in children:
    child.kill()
print(len(children), file=sys.stderr)
assert len(children) < 64
with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    output.write(source.read())
"""


@real
@pytest.mark.parametrize("code", [SANDBOX, PROCESSES], ids=["host-boundaries", "process-limit"])
def test_a_script_cannot_leave_its_sandbox(settings, code):
    job, dataset, before = run_case(settings, [single()], {"step.py": code})
    assert job["status"] == "completed", job["details"].get("error")
    assert len(cell_fingerprints(dataset)) == len(before) + 1


@real
@pytest.mark.parametrize(
    ("code", "limits", "message"),
    [
        (
            "import os, sys\nos.remove(sys.argv[2]) if os.path.exists(sys.argv[2]) else None\n"
            "os.symlink('/etc/passwd', sys.argv[2])\n",
            None,
            "artifact transfer failed with exit code 45",
        ),
        (
            "open('/work/fill.bin', 'wb').write(b'x' * (60 * 1024 * 1024))\n" + COPY,
            {"seconds": 60, "scratch_mb": 16},
            "exited with code 1",
        ),
        (
            "import json, sys\nopen(sys.argv[2], 'w').write("
            "json.dumps({'value': 1, 'source_row': 0, 'blob': 'x' * (5 * 1024 * 1024)}) + '\\n')\n",
            None,
            "An output row exceeds 4 MiB.",
        ),
        (
            "import json, sys\nrow = {f'c{i}': i for i in range(201)}\nrow['source_row'] = 0\n"
            "open(sys.argv[2], 'w').write(json.dumps(row) + '\\n')\n",
            None,
            "exceeds 200 columns",
        ),
    ],
    ids=["output-symlink-escape", "scratch-limit", "oversize-row", "too-many-columns"],
)
def test_runtime_limits_fail_the_run_and_publish_nothing(settings, code, limits, message):
    job, dataset, before = run_case(settings, [single()], {"step.py": code}, limits=limits)
    assert_nothing_published(job, dataset, before, message)


@real
@pytest.mark.parametrize(
    ("branch_b", "message"),
    [
        (COPY, "overlapping source_row"),
        (
            "import json, sys\nwith open(sys.argv[1]) as s, open(sys.argv[2], 'w') as o:\n"
            "    for line in s:\n        row = json.loads(line)\n"
            "        if row['value'] % 2:\n            row['value'] = str(row['value'])\n"
            "            o.write(json.dumps(row) + '\\n')\n",
            "incompatible columns or types",
        ),
    ],
    ids=["overlapping-branches", "incompatible-branches"],
)
def test_invalid_fan_in_fails_in_the_real_runtime(settings, branch_b, message):
    even = (
        "import json, sys\nwith open(sys.argv[1]) as s, open(sys.argv[2], 'w') as o:\n"
        "    for line in s:\n        if json.loads(line)['value'] % 2 == 0:\n"
        "            o.write(line)\n"
    )
    steps = [
        {**single("a.py"), "id": "a"},
        {**single("b.py"), "id": "b", "output_schema": {"value": "any", "source_row": "integer"}},
        {**single("merge.py"), "id": "merge", "inputs": ["a", "b"]},
    ]
    del steps[2]["input"]
    job, dataset, before = run_case(
        settings, steps, {"a.py": even, "b.py": branch_b, "merge.py": COPY}
    )
    assert_nothing_published(job, dataset, before, message)


DROP_ONE = (
    "import sys\nwith open(sys.argv[1]) as s, open(sys.argv[2], 'w') as o:\n"
    "    o.writelines(list(s)[1:])\n"
)


@real
@pytest.mark.parametrize(
    ("checks", "code", "failed"),
    [
        ({"min_rows": 1000}, COPY, {"min_rows": {"expected": 1000, "actual": 10}}),
        ({"max_rows": 5}, COPY, {"max_rows": {"expected": 5, "actual": 10}}),
        ({"preserve_rows": True}, DROP_ONE, {"preserve_rows": {"expected": 10, "actual": 9}}),
    ],
    ids=["min-rows", "max-rows", "preserve-rows"],
)
def test_failed_row_checks_publish_nothing(settings, checks, code, failed):
    job, dataset, before = run_case(settings, [single(checks=checks)], {"step.py": code})
    assert_nothing_published(job, dataset, before, "row checks failed")
    assert published_steps(job)["step"]["check_results"]["failed"] == failed


@real
def test_a_failing_batch_publishes_no_partial_output(settings):
    code = (
        "import json, sys\nwith open(sys.argv[1]) as s, open(sys.argv[2], 'w') as o:\n"
        "    for line in s:\n        if json.loads(line)['value'] >= 8:\n"
        "            sys.exit(3)\n        o.write(line)\n"
    )
    job, dataset, before = run_case(settings, [single(batch_rows=4)], {"step.py": code})
    assert_nothing_published(job, dataset, before, "exited with code 3")
    assert published_steps(job)["step"]["batches"]["completed"] == 2


@real
def test_cancelling_a_running_container_stops_it_and_publishes_nothing(settings):
    runtime = use_runtime(settings)
    project, client, context = workspace()
    dataset = source(client, project, "Cancel", [{"value": index} for index in range(10)])
    original = dataset.active_cell
    files = {
        "manifest.json": json.dumps(
            {"version": 1, "runtime": runtime, "limits": {"seconds": 120}, "steps": [single()]}
        ),
        "step.py": "import time\ntime.sleep(90)\n" + COPY,
    }
    package = register(client, project, files)
    recipe = call(
        context, "save_dataset_pipeline", name="Cancel", request_key="cancel", package=package["id"]
    )["pipeline"]
    before = cell_fingerprints(dataset)
    run = submit(context, dataset, recipe, "cancel", original)

    def execute_in_background():
        try:
            workbench.execute(run["id"], script_executor=pipeline_runner.execute_script)
        finally:
            connection.close()

    worker = threading.Thread(target=execute_in_background)
    worker.start()
    deadline = time.monotonic() + 60
    while True:
        steps = DatasetPipelineRun.objects.get(pk=run["id"]).result.get("steps") or [{}]
        if steps[0].get("runtime", {}).get("stage") == "script_execution":
            break
        assert time.monotonic() < deadline, "the script never started executing"
        time.sleep(0.2)
    assert leftover_containers(run["id"])
    cancelled_at = time.monotonic()
    cancelled = call(context, "cancel_dataset_pipeline_run", run=run["id"])["run"]
    assert cancelled["state"] == "cancelled", cancelled
    worker.join(timeout=60)
    assert not worker.is_alive()
    assert time.monotonic() - cancelled_at < 30
    assert leftover_containers(run["id"]) == []
    job = finish(context, run)
    assert job["status"] == "cancelled", job
    assert cell_fingerprints(dataset) == before
    dataset.refresh_from_db()
    assert dataset.state == "idle"
