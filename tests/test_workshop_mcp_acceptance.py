import asyncio
import json
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.utils import timezone
from test_workshop_redesign import call, workspace

from overbae.models import Dataset, DatasetPipelineRun
from overbae.services.datasets import dispatch, paths, store, workbench
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import bind_context
from overbae.services.mcp.resources import read_resource
from overbae.tasks.datasets import land
from tests.workshop_script_fixtures import (
    execute_fixture,
    filter_script,
    retained_package,
    select_script,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("mode", ["preview", "publish"])
def test_pipeline_poll_and_replay_keep_examples_in_drilldown_not_status(mode):
    project, client, context = workspace()
    created = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "source": {"rows": [{"text": "evidence " * 600} for _ in range(5)]},
        },
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    source = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        {
            "name": "Receipt size",
            "request_key": "receipt-size",
            "package": retained_package(
                context.project, context.user, [select_script(["text"])] * 2
            ),
        },
    )["pipeline"]
    arguments = {
        "dataset": str(dataset.pk),
        "pipeline": recipe["id"],
        "source_cell": str(source.pk),
        "source_fingerprint": source.fingerprint,
        "request_key": "receipt-size-run",
        "mode": mode,
    }
    run = call(context, "run_dataset_pipeline", arguments)["run"]
    workbench.execute(run["id"], script_executor=execute_fixture)
    recorded = DatasetPipelineRun.objects.get(pk=run["id"])
    original_result = json.dumps(recorded.result, sort_keys=True)
    job = call(context, "get_job", {"kind": "dataset_pipeline", "id": run["id"]})
    repeated = call(context, "run_dataset_pipeline", arguments)
    bench = call(context, "inspect_dataset_workbench", {"dataset": str(dataset.pk), "limit": 1})
    for response in (job, repeated, bench):
        assert len(json.dumps(response).encode()) < 48000
    assert job["progress"]["steps"][0]["output_rows"] == 5
    assert job["progress"]["seconds"] > 0
    assert "input_examples" not in job["progress"]["steps"][0]["impact"]
    assert job["details"]["evidence_resource"]["uri"] == job["resource"]["uri"]

    async def evidence():
        with bind_context(context):
            contents = list(await read_resource(job["resource"]["uri"]))
        return json.loads(contents[0].content)

    retained = asyncio.run(evidence())
    assert retained["result"] == recorded.result
    recorded.refresh_from_db()
    assert json.dumps(recorded.result, sort_keys=True) == original_result


def test_query_deadline_preserves_source_and_next_read(settings):
    project, client, context = workspace()
    created = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "source": {"rows": [{"value": 17}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    original = dataset.active_cell.fingerprint
    settings.DATASET_QUERY_TIMEOUT_SECONDS = 0.001
    result = asyncio.run(
        CATALOG.call(
            "query_dataset",
            {"dataset": str(dataset.pk), "sql": "SELECT sum(hash(i)) FROM range(10000000) t(i)"},
            context,
        )
    )
    assert result.isError, result.structuredContent
    assert result.structuredContent["error"]["code"] == "query_timeout"
    settings.DATASET_QUERY_TIMEOUT_SECONDS = 10
    result = call(
        context, "query_dataset", {"dataset": str(dataset.pk), "sql": "SELECT value FROM t"}
    )
    assert result["rows"] == [{"value": 17}]
    dataset.refresh_from_db()
    assert dataset.active_cell.fingerprint == original


def test_unsafe_query_and_llm_call_source_return_public_mcp_contracts():
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "source": {"rows": [{"input": "Question", "expected_output": "Answer"}]},
            "intent": "eval",
        },
        format="json",
    )
    dataset = Dataset.objects.get(pk=response.data["id"])
    before = dataset.active_cell.fingerprint
    for sql in (
        "SELECT 1; SELECT 2",
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "DELETE FROM t",
    ):
        result = asyncio.run(
            CATALOG.call("query_dataset", {"dataset": str(dataset.pk), "sql": sql}, context)
        )
        assert result.isError
        assert result.structuredContent["error"]["code"] == "query_invalid"
    Dataset.objects.filter(pk=dataset.pk).update(source_kind=Dataset.SourceKind.LLM_CALLS)
    detail = call(context, "inspect_dataset", {"dataset": str(dataset.pk)})
    assert detail["source_kind"] == "llm_calls"
    assert detail["active"]["fingerprint"] == before
    listed = call(context, "list_datasets", {})
    assert listed["datasets"][0]["source_kind"] == "llm_calls"


@pytest.mark.parametrize("filename_prefix", ["scanned", "扫描文件" * 30], ids=["ascii", "unicode"])
def test_dense_document_metadata_pages_without_losing_extraction_facts(filename_prefix):
    project, client, context = workspace()
    created = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "source": {"rows": [{"text": "Evidence"}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    sources = [
        {
            "id": f"{index:064x}",
            "sha256": f"{index:064x}",
            "filename": f"{filename_prefix}-{index}.pdf",
            "bytes": 1000,
            "rows": 2000,
            "extraction": {
                "method": "docling-native-pdf+tesseract-ocr",
                "version": "test",
                "pages": 2000,
                "limitations": ["OCR uses English language data and may misread text."],
                "ocr": {
                    "engine": "tesseract",
                    "version": "test",
                    "languages": ["eng"],
                    "pages": list(range(1, 2001)),
                },
                "native_text_recovery": {
                    "method": "pdfium-native-text",
                    "version": "test",
                    "pages": list(range(1, 2001)),
                    "rows": 2000,
                    "characters": 200000,
                    "control_characters": 3,
                    "reason": "primary_parser_returned_no_text",
                },
            },
        }
        for index in range(25)
    ]
    Dataset.objects.filter(pk=dataset.pk).update(source_spec={"sources": sources})
    found = []
    offset = 0
    while True:
        result = call(
            context,
            "inspect_dataset",
            {
                "dataset": str(dataset.pk),
                "source_offset": offset,
                "source_limit": 20,
            },
        )
        assert len(json.dumps(result).encode()) <= 32_000
        found.extend(result["sources"])
        page = result["source_page"]
        assert page["total"] == 25 and page["offset"] == offset
        if not page["has_more"]:
            break
        assert int(page["next_cursor"]) > offset
        offset = int(page["next_cursor"])
    assert found == sources
    exhausted = call(context, "inspect_dataset", {"dataset": str(dataset.pk), "source_offset": 25})
    assert exhausted["sources"] == [] and not exhausted["source_page"]["has_more"]


def test_landing_job_reports_persisted_facts_without_using_old_version_rows_as_progress():
    project, client, context = workspace()
    created = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "source": {"rows": [{"text": "Prior"}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    progress = {
        "filename": "scan.pdf",
        "completed": 2,
        "total": 3,
        "stage": "ocr",
        "pages_total": 25,
        "ocr_pages_completed": 7,
        "ocr_pages_total": 25,
    }
    Dataset.objects.filter(pk=dataset.pk).update(
        state="landing", source_spec={"landing_progress": progress}
    )
    job = call(context, "get_job", {"kind": "dataset_run", "id": str(dataset.pk)})
    assert job["progress"]["landing"] == progress
    assert job["progress"]["rows"] == 1
    detail = call(context, "inspect_dataset", {"dataset": str(dataset.pk)})
    assert detail["landing_progress"] == progress
    Dataset.objects.filter(pk=dataset.pk).update(state="error")
    with patch.object(land, "apply_async"):
        attached = client.post(
            f"/api/datasets/{dataset.pk}/source/",
            {"rows": [{"text": "Replacement"}]},
            format="json",
        )
    assert attached.status_code == 202
    pending = call(context, "inspect_dataset", {"dataset": str(dataset.pk)})
    assert pending["landing_progress"] is None


def test_pipeline_job_exposes_measured_stage_and_completion_counts():
    project, client, context = workspace()
    created = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "source": {"rows": [{"text": "Keep", "keep": True}, {"text": "Drop", "keep": False}]},
        },
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    source = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        {
            "dataset": str(dataset.pk),
            "name": "Filter",
            "request_key": "progress",
            "package": retained_package(
                context.project, context.user, [filter_script("keep", True)]
            ),
        },
    )
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        run = call(
            context,
            "run_dataset_pipeline",
            {
                "dataset": str(dataset.pk),
                "pipeline": recipe["pipeline"]["id"],
                "source_cell": str(source.pk),
                "source_fingerprint": source.fingerprint,
                "request_key": "progress",
            },
        )
    impact = workbench.review.impact_files
    observations = []

    def inspect_impact(*args, **kwargs):
        observations.append(
            call(context, "get_job", {"kind": "dataset_pipeline", "id": run["run"]["id"]})
        )
        return impact(*args, **kwargs)

    with patch.object(workbench.review, "impact_files", side_effect=inspect_impact):
        workbench.execute(run["run"]["id"], script_executor=execute_fixture)
    assert observations[0]["progress"]["stage"] == "measuring_impact"
    assert observations[0]["progress"]["source_rows"] == 2
    assert observations[0]["progress"]["output_rows"] == 1
    completed = call(context, "get_job", {"kind": "dataset_pipeline", "id": run["run"]["id"]})
    assert completed["status"] == "completed"
    assert completed["progress"]["stage"] == "completed"
    assert completed["progress"]["source_rows"] == 2 and completed["progress"]["rows"] == 1


def test_cancelled_initial_delivery_cannot_replace_new_attachment(
    django_capture_on_commit_callbacks,
):
    project, client, _ = workspace()
    with patch.object(land, "apply_async") as dispatch:
        response = client.post(
            "/api/datasets/",
            {
                "project": str(project.pk),
                "name": "Delayed source",
                "source": {"rows": [{"text": "cancelled old source"}]},
            },
            format="json",
        )
        original = dispatch.call_args.kwargs["kwargs"]
        dataset = Dataset.objects.get(pk=response.data["id"])
        assert (
            client.post(f"/api/datasets/{dataset.pk}/cancel/", {}, format="json").status_code == 202
        )
        with django_capture_on_commit_callbacks(execute=True):
            attached = client.post(
                f"/api/datasets/{dataset.pk}/source/",
                {"rows": [{"text": "replacement"}]},
                format="json",
            )
        assert attached.status_code == 202
        replacement = dispatch.call_args.kwargs["kwargs"]
    assert replacement["attachment_request"]
    land.run(**original)
    dataset.refresh_from_db()
    assert dataset.state == "landing" and dataset.active_cell is None
    land.run(**replacement)
    dataset.refresh_from_db()
    assert dataset.state == "idle"
    assert (
        list(store.iter_rows(paths.cell_path(dataset.pk, dataset.active_cell.pk)))[0]["text"]
        == "replacement"
    )


def test_workbench_pages_every_immutable_recipe_without_losing_older_records():
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "brief": "Inspect a long transformation history"},
        format="json",
    )
    dataset = response.data["id"]
    saved = set()
    for index in range(105):
        result = call(
            context,
            "save_dataset_pipeline",
            {
                "dataset": dataset,
                "name": f"Selection {index}",
                "request_key": uuid.uuid4().hex,
                "package": retained_package(
                    context.project, context.user, [select_script(["text"])]
                ),
            },
        )
        saved.add(result["pipeline"]["id"])
    found = []
    offset = 0
    while True:
        result = call(
            context,
            "inspect_dataset_workbench",
            {"dataset": dataset, "pipeline_offset": offset, "limit": 20},
        )
        found.extend(row["id"] for row in result["pipelines"])
        page = result["pipeline_page"]
        assert page["total"] == 105
        if not page["has_more"]:
            break
        offset = int(page["next_cursor"])
    assert len(found) == len(set(found)) == 105
    assert set(found) == saved


@pytest.mark.parametrize("interruption", ["cancel", "expire"])
def test_interrupted_worker_cannot_publish_over_a_newer_queued_run(interruption):
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "source": {"rows": [{"text": "preserved"}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=response.data["id"])
    source = dataset.active_cell
    original = paths.cell_path(dataset.pk, source.pk).read_bytes()
    recipe = call(
        context,
        "save_dataset_pipeline",
        {
            "dataset": str(dataset.pk),
            "name": "Select",
            "request_key": "select",
            "package": retained_package(context.project, context.user, [select_script(["text"])]),
        },
    )
    args = {
        "dataset": str(dataset.pk),
        "pipeline": recipe["pipeline"]["id"],
        "source_cell": str(source.pk),
        "source_fingerprint": source.fingerprint,
        "request_key": "interrupted",
    }
    replacement = []
    impact = workbench.review.impact_files

    def interrupt(*positional, **keyword):
        if interruption == "cancel":
            assert (
                client.post(f"/api/datasets/{dataset.pk}/cancel/", {}, format="json").status_code
                == 202
            )
        else:
            DatasetPipelineRun.objects.filter(pk=first["run"]["id"]).update(
                lease_until=timezone.now() - timedelta(seconds=1)
            )
            workbench.expire_runs()
        replacement.append(
            call(context, "run_dataset_pipeline", {**args, "request_key": "replacement"})
        )
        return impact(*positional, **keyword)

    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        first = call(context, "run_dataset_pipeline", args)
        with patch.object(workbench.review, "impact_files", side_effect=interrupt):
            workbench.execute(first["run"]["id"], script_executor=execute_fixture)
    dataset.refresh_from_db()
    assert dataset.active_cell.pk == source.pk and dataset.state == "running"
    assert dataset.cells.count() == 1
    workbench.execute(first["run"]["id"], script_executor=execute_fixture)
    assert dataset.cells.count() == 1
    workbench.execute(replacement[0]["run"]["id"], script_executor=execute_fixture)
    workbench.execute(replacement[0]["run"]["id"], script_executor=execute_fixture)
    dataset.refresh_from_db()
    assert dataset.state == "idle" and dataset.cells.count() == 2
    assert paths.cell_path(dataset.pk, source.pk).read_bytes() == original
    states = dict(dataset.pipeline_runs.values_list("request_key", "state"))
    assert states == {
        "interrupted": "cancelled" if interruption == "cancel" else "failed",
        "replacement": "completed",
    }


def test_workbench_run_history_and_rest_pagination_remain_complete():
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "source": {"rows": [{"text": "evidence"}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=response.data["id"])
    source = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        {
            "dataset": str(dataset.pk),
            "name": "Select",
            "request_key": "recipe",
            "package": retained_package(context.project, context.user, [select_script(["text"])]),
        },
    )
    saved = set()
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        for index in range(105):
            receipt = call(
                context,
                "run_dataset_pipeline",
                {
                    "dataset": str(dataset.pk),
                    "pipeline": recipe["pipeline"]["id"],
                    "source_cell": str(source.pk),
                    "source_fingerprint": source.fingerprint,
                    "request_key": f"cancelled-{index}",
                },
            )
            saved.add(receipt["run"]["id"])
            workbench.cancel(dataset)
    found = []
    for offset in range(0, 120, 20):
        mcp = call(
            context,
            "inspect_dataset_workbench",
            {"dataset": str(dataset.pk), "run_offset": offset, "limit": 20},
        )
        rest = client.get(
            f"/api/datasets/{dataset.pk}/workbench/", {"run_offset": offset, "limit": 20}
        )
        assert rest.status_code == 200
        assert dict(rest.data["run_page"]) == mcp["run_page"]
        assert [row["id"] for row in rest.data["runs"]] == [row["id"] for row in mcp["runs"]]
        found.extend(row["id"] for row in mcp["runs"])
    assert len(found) == len(set(found)) == 105 and set(found) == saved
    assert client.get(f"/api/datasets/{dataset.pk}/workbench/", {"limit": 101}).status_code == 400
    assert dataset.cells.count() == 1


def test_cancelled_split_member_does_not_strand_its_partner():
    project, client, context = workspace()
    with patch.object(land, "apply_async") as queued:
        train, evaluation = dispatch.create_split(
            project=project,
            user=context.user,
            name="Paired source",
            source={"rows": [{"input": f"Q{i}", "expected_output": f"A{i}"} for i in range(10)]},
            eval_percent=20,
            position="tail",
        )
    payload = queued.call_args.kwargs["kwargs"]
    assert (
        client.post(f"/api/datasets/{evaluation.pk}/cancel/", {}, format="json").status_code == 202
    )
    land.run(**payload)
    train.refresh_from_db()
    evaluation.refresh_from_db()
    assert train.state == "error"
    assert evaluation.state == "idle" and evaluation.operation["state"] == "cancelled"
    assert not train.cells.exists() and not evaluation.cells.exists()
    assert (
        client.post(
            f"/api/datasets/{train.pk}/source/", {"rows": [{"text": "retry"}]}, format="json"
        ).status_code
        == 202
    )
    train.refresh_from_db()
    assert train.state == "idle" and train.active_cell.rows == 1


@pytest.mark.parametrize(
    "values",
    [
        [None, 0, False, 1, True, "", "0", "False"],
        [2**63 - 1, 2**63, 10**40, -(10**40)],
        [2**53 + 1, 1.25],
        [None, 2**53 + 1],
        [0] * 10001 + [False, "0"],
    ],
    ids=[
        "mixed-scalars",
        "large-integers",
        "float-precision",
        "nullable-integer",
        "late-type-change",
    ],
)
def test_landing_and_pipeline_preserve_scalar_values_without_lossy_coercion(values):
    project, client, context = workspace()
    created = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "source": {"rows": [{"value": v} for v in values]}},
        format="json",
    )
    assert created.status_code == 201, created.data
    dataset = Dataset.objects.get(pk=created.data["id"])
    assert dataset.state == "idle", dataset.error
    source = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        {
            "name": "Retain values",
            "request_key": "scalar-copy",
            "package": retained_package(context.project, context.user, [select_script(["value"])]),
        },
    )["pipeline"]
    run = call(
        context,
        "run_dataset_pipeline",
        {
            "dataset": str(dataset.pk),
            "pipeline": recipe["id"],
            "source_cell": str(source.pk),
            "source_fingerprint": source.fingerprint,
            "request_key": "scalar-copy-run",
        },
    )["run"]
    workbench.execute(run["id"], script_executor=execute_fixture)
    dataset.refresh_from_db()
    assert dataset.state == "idle", dataset.error
    assert dataset.active_cell.pk != source.pk
    for cell in (source, dataset.active_cell):
        actual = [r["value"] for r in store.iter_rows(paths.cell_path(dataset.pk, cell.pk))]
        assert json.dumps(actual) == json.dumps(values)
        frame_values = store.read_frame(paths.cell_path(dataset.pk, cell.pk))["value"].tolist()
        assert json.dumps(frame_values) == json.dumps(values)
        batches = store.iter_frames(paths.cell_path(dataset.pk, cell.pk), batch_size=10000)
        assert json.dumps(
            [value for frame in batches for value in frame["value"].tolist()]
        ) == json.dumps(values)
        result = call(
            context,
            "query_dataset",
            {
                "dataset": str(dataset.pk),
                "cell": str(cell.pk),
                "sql": "SELECT value FROM t ORDER BY source_row DESC",
                "limit": 10,
            },
        )
        assert json.dumps([r["value"] for r in result["rows"]]) == json.dumps(
            list(reversed(values))[:10]
        )


def test_wide_query_fails_with_actionable_size_error_without_clipping_values():
    project, client, context = workspace()
    text = "東京" * 100000
    created = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "source": {"rows": [{"text": text}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    result = asyncio.run(
        CATALOG.call(
            "query_dataset", {"dataset": str(dataset.pk), "sql": "SELECT text FROM t"}, context
        )
    )
    assert result.isError
    assert result.structuredContent["error"]["code"] == "query_result_too_large"
    assert "export" in result.structuredContent["error"]["message"]
    assert len(result.model_dump_json().encode()) < 4000
    projected = call(
        context,
        "query_dataset",
        {"dataset": str(dataset.pk), "sql": "SELECT length(text) AS characters FROM t"},
    )
    assert projected["rows"] == [{"characters": len(text)}]
    assert store.read_row(paths.cell_path(dataset.pk, dataset.active_cell.pk), 0)["text"] == text


def test_wide_schema_query_does_not_omit_column_metadata():
    project, client, context = workspace()
    row = {f"column_{i}": i for i in range(201)}
    created = client.post(
        "/api/datasets/", {"project": str(project.pk), "source": {"rows": [row]}}, format="json"
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    result = asyncio.run(
        CATALOG.call(
            "query_dataset", {"dataset": str(dataset.pk), "sql": "SELECT * FROM t"}, context
        )
    )
    assert result.isError
    assert result.structuredContent["error"]["code"] == "query_result_too_large"
    projected = call(
        context,
        "query_dataset",
        {"dataset": str(dataset.pk), "sql": "SELECT column_0, column_200 FROM t"},
    )
    assert projected["columns"] == ["column_0", "column_200"]
    assert projected["rows"] == [{"column_0": 0, "column_200": 200}]


def test_mcp_query_round_trips_native_json_without_decoding_text_columns():
    project, client, context = workspace()
    decision = {
        "state": "",
        "question": "Choose",
        "kind": "choice",
        "options": ["B", "A"],
        "target_probabilities": [0.3, 0.7],
        "weight": 2.5,
    }
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "intent": "train",
            "source": {
                "rows": [
                    {"decision": decision, "literal": '{"not":"a typed object"}'},
                    {"decision": decision, "literal": "null"},
                ]
            },
        },
        format="json",
    )
    dataset = Dataset.objects.get(pk=response.data["id"])
    source = dataset.active_cell
    rows = call(
        context,
        "query_dataset",
        {
            "dataset": str(dataset.pk),
            "sql": "SELECT source_row, decision AS native, decision.options AS options, literal FROM t ORDER BY source_row",
        },
    )["rows"]
    assert rows == [
        {
            "source_row": 0,
            "native": decision,
            "options": ["B", "A"],
            "literal": '{"not":"a typed object"}',
        },
        {"source_row": 1, "native": decision, "options": ["B", "A"], "literal": "null"},
    ]
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        receipt = call(
            context,
            "import_dataset_version",
            {
                "dataset": str(dataset.pk),
                "source_cell": str(source.pk),
                "source_fingerprint": source.fingerprint,
                "name": "Preserved decisions",
                "request_key": "query-copy",
                "provenance": "Identity copy through MCP query.",
                "imported_rows": [
                    {
                        "source_row": row["source_row"],
                        "decision": row["native"],
                        "literal": row["literal"],
                    }
                    for row in rows
                ],
            },
        )
    workbench.execute(receipt["run"]["id"], script_executor=execute_fixture)
    dataset.refresh_from_db()
    imported = list(store.iter_rows(paths.cell_path(dataset.pk, dataset.active_cell.pk)))
    assert [row["decision"] for row in imported] == [decision, decision]
    assert [row["literal"] for row in imported] == ['{"not":"a typed object"}', "null"]


def test_document_source_identifiers_survive_bounded_mcp_metadata():
    project, client, context = workspace()
    content = (Path(__file__).parent / "fixtures" / "documents" / "scanned.pdf").read_bytes()
    reserved = client.post("/api/uploads/", {"filename": "scanned.pdf"}, format="json")
    assert reserved.status_code == 201, reserved.data
    identifier = reserved.data["upload_id"]
    assert (
        client.put(
            f"/api/uploads/{identifier}/chunk/?offset=0",
            content,
            content_type="application/octet-stream",
        ).status_code
        == 200
    )
    inspected = client.post(
        f"/api/uploads/{identifier}/inspect/", {"size": len(content)}, format="json"
    )
    assert inspected.status_code == 200, inspected.data
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "source": {"uploads": [identifier]},
        },
        format="json",
    )
    assert response.status_code == 201
    dataset = response.data["id"]
    detail = call(context, "inspect_dataset", {"dataset": dataset})
    source = detail["sources"][0]
    downloaded = client.get(f"/api/datasets/{dataset}/sources/{source['id']}/")
    assert downloaded.status_code == 200, source
    assert b"".join(downloaded.streaming_content) == content
