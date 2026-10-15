from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from factories import make_matrix_row
from mcp.shared.exceptions import McpError
from mcp_fixtures import mcp_context

from overbae.innermind import runs
from overbae.models import (
    Capability,
    Cell,
    Dataset,
    EvalMatrix,
    EvalRun,
    EvalRunRow,
    EvalScore,
)
from overbae.services.datasets import paths, store
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import read_resource
from overbae.tasks.innermind import run_eval_run

pytestmark = pytest.mark.django_db(transaction=True)

EVALUATION_TOOLS = {
    "create_eval_matrix_row",
    "delete_eval_matrix_row",
    "create_eval_set",
    "check_evaluation_readiness",
    "run_evaluation",
    "compare_evaluations",
}
EXACT_MATCH = "def evaluate(span):\n    return span.output == span.expected\n"


def _call(name: str, arguments: dict, context: MCPContext):
    return asyncio.run(CATALOG.call(name, arguments, context))


def _read(context: MCPContext, uri: str) -> dict:
    with bind_context(context):
        return json.loads(asyncio.run(read_resource(uri))[0].content)


def _dataset(context: MCPContext, rows: int = 2) -> Dataset:
    dataset = Dataset.objects.create(project=context.project, name="Eval", intent="eval")
    cell = Cell.objects.create(
        dataset=dataset,
        position=0,
        title="source",
        state=Cell.State.OK,
        rows=rows,
        intent_report={"eval": {"ok": True, "reason": ""}},
        capability_report={"ok": True, "reason": ""},
    )
    path = paths.cell_path(dataset.id, cell.id)
    store.write_rows(
        path,
        [{"source_row": i, "input": f"q{i}", "expected_output": f"a{i}"} for i in range(rows)],
    )
    cell.fingerprint = store.file_sha256(path)
    cell.save(update_fields=["fingerprint"])
    dataset.active = cell
    dataset.save(update_fields=["active"])
    return dataset


def _writer() -> MCPContext:
    return mcp_context(permission=["read", "write"])


def test_catalog_exposes_evaluation_tools_and_hides_writes_from_readers():
    assert {definition.name for definition in CATALOG.definitions()} >= EVALUATION_TOOLS
    readable = {tool.name for tool in CATALOG.tools(frozenset({"read"}))}
    assert readable & EVALUATION_TOOLS == {"check_evaluation_readiness", "compare_evaluations"}

    result = _call(
        "create_eval_matrix_row", {"name": "Hidden", "type": "llm", "source": "x"}, mcp_context()
    )
    assert result.structuredContent["error"]["code"] == "permission_denied"
    result = _call("delete_eval_matrix_row", {"row": "Hidden"}, mcp_context())
    assert result.structuredContent["error"]["code"] == "permission_denied"


def test_matrix_rows_are_created_and_deleted_but_never_edited():
    context = _writer()
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")

    created = _call(
        "create_eval_matrix_row",
        {
            "name": "Exact",
            "type": "deterministic",
            "source": EXACT_MATCH,
            "capability": capability.slug,
        },
        context,
    )
    escaping = _call(
        "create_eval_matrix_row",
        {
            "name": "Escape",
            "type": "deterministic",
            "source": "import os\ndef evaluate(span):\n    return os.getcwd()\n",
        },
        context,
    )
    edit = _call(
        "create_eval_matrix_row",
        {"row": "Exact", "name": "Exact", "type": "llm", "source": "x"},
        context,
    )

    assert created.structuredContent["row"]["capability"] == "support"
    assert created.structuredContent["row"]["definition_source"] == "custom"
    assert escaping.structuredContent["error"]["code"] == "eval_matrix_row_invalid"
    assert not EvalMatrix.objects.filter(name="Escape").exists()
    assert edit.isError

    row = EvalMatrix.objects.get(name="Exact")
    eval_set = _call(
        "create_eval_set",
        {"name": "Quality", "capability": "support", "rows": ["Exact"]},
        context,
    )
    deleted = _call("delete_eval_matrix_row", {"row": "Exact", "capability": "support"}, context)

    assert deleted.structuredContent["row"]["id"] == str(row.id)
    assert not EvalMatrix.objects.filter(id=row.id).exists()
    assert _read(context, eval_set.structuredContent["resource_links"][0]["uri"])["rows"] == []
    missing = _call("delete_eval_matrix_row", {"row": "Exact"}, context)
    assert missing.structuredContent["error"]["code"] == "eval_matrix_row_not_found"


def test_matrix_rows_cannot_be_deleted_across_projects():
    context = _writer()
    other = _writer()
    row = make_matrix_row(other.project, "Theirs")

    result = _call("delete_eval_matrix_row", {"row": str(row.id)}, context)

    assert result.structuredContent["error"]["code"] == "eval_matrix_row_not_found"
    assert EvalMatrix.objects.filter(id=row.id).exists()


def test_eval_sets_are_project_scoped_and_can_be_activated():
    context = _writer()
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    row = make_matrix_row(context.project, "Accuracy")

    result = _call(
        "create_eval_set",
        {"name": "Quality", "capability": "support", "rows": ["Accuracy"], "activate": True},
        context,
    )

    assert result.structuredContent["eval_set"]["active"] is True
    capability.refresh_from_db()
    assert capability.active_eval_set.rows.get() == row
    resource = _read(context, result.structuredContent["resource_links"][0]["uri"])
    assert [entry["name"] for entry in resource["rows"]] == ["Accuracy"]
    other = _writer()
    with bind_context(other), pytest.raises(McpError):
        asyncio.run(read_resource(result.structuredContent["resource_links"][0]["uri"]))
    foreign = _call("create_eval_set", {"name": "Foreign", "rows": [str(row.id)]}, other)
    assert foreign.structuredContent["error"]["code"] == "eval_matrix_row_not_found"


def test_readiness_reports_advisory_context_without_blocking(fake_llm):
    fake_llm.limits["openai/gpt-4.1"] = 1000
    context = mcp_context()
    dataset = _dataset(context)
    make_matrix_row(
        context.project, "Exact", type=EvalMatrix.Type.DETERMINISTIC, source=EXACT_MATCH
    )

    result = _call(
        "check_evaluation_readiness",
        {
            "dataset": str(dataset.pk),
            "targets": [{"mode": "generate", "label": "Candidate", "model_name": "gpt-4.1"}],
        },
        context,
    )

    output = result.structuredContent
    [check] = output["context_checks"]
    assert (check["role"], check["label"], check["status"]) == (
        "generation",
        "Candidate",
        "warning",
    )
    assert check["context_window"] == 1000
    assert output["matrix"] == [{"name": "Exact", "type": "deterministic"}]
    assert output["ready"] is True


def test_readiness_without_matrix_rows_is_not_ready():
    context = mcp_context()
    dataset = _dataset(context)

    output = _call(
        "check_evaluation_readiness",
        {"dataset": str(dataset.pk), "targets": [{"mode": "recorded"}]},
        context,
    ).structuredContent

    assert output["ready"] is False
    assert output["matrix"] == []


def test_run_evaluation_queues_one_run_per_target_in_one_group(monkeypatch):
    queued = []
    monkeypatch.setattr(run_eval_run, "delay", lambda **kw: queued.append(kw) or _Result())
    context = _writer()
    dataset = _dataset(context)
    make_matrix_row(
        context.project, "Exact", type=EvalMatrix.Type.DETERMINISTIC, source=EXACT_MATCH
    )

    result = _call(
        "run_evaluation",
        {
            "name": "Backtest",
            "dataset": str(dataset.pk),
            "judge_model": "claude-haiku-4-5",
            "targets": [
                {"mode": "recorded", "label": "Recorded", "is_baseline": True},
                {"mode": "generate", "label": "Mini", "model_name": "openai/gpt-5-mini"},
            ],
        },
        context,
    )

    output = result.structuredContent
    assert [(run["label"], run["mode"], run["is_baseline"]) for run in output["runs"]] == [
        ("Recorded", "recorded", True),
        ("Mini", "generate", False),
    ]
    created = EvalRun.objects.filter(group=output["group"])
    assert created.count() == 2
    assert set(created.values_list("judge_model", flat=True)) == {"claude-haiku-4-5"}
    assert {kw["eval_run_id"] for kw in queued} == {run["id"] for run in output["runs"]}
    resource = _read(context, output["runs"][1]["resource"]["uri"])
    assert resource["matrix"] == [{"name": "Exact", "type": "deterministic"}]
    assert _call("run_evaluation", {**_run_args(dataset)}, _writer()).isError


def _run_args(dataset) -> dict:
    return {"name": "x", "dataset": str(dataset.pk), "targets": [{"mode": "recorded"}]}


class _Result:
    id = "task"


def _completed_run(context, dataset, label, row_scores, *, group, is_baseline=False) -> EvalRun:
    row = make_matrix_row(context.project, f"Exact {label}")
    run = runs.create_run(
        project=context.project,
        name=label,
        cell=dataset.active_cell,
        mode=EvalRun.Mode.PROVIDED,
        matrix=[row.snapshot()],
        group=group,
        is_baseline=is_baseline,
        params={"label": label},
    )
    for index, score in enumerate(row_scores):
        EvalRunRow.objects.create(run=run, row_index=index, output="x", state="done")
        EvalScore.objects.create(
            project=context.project,
            source_type=EvalScore.SourceType.DATASET_ROW,
            source_id=f"{run.cell_id}:{index}",
            run=run,
            score=score,
            metrics={"Exact": {"type": "llm", "score": score}},
        )
    runs.finish(run)
    return run


def test_compare_pairs_rows_against_the_group_baseline():
    context = mcp_context()
    dataset = _dataset(context)
    group = uuid.uuid4()
    _completed_run(context, dataset, "Base", [0.5, 1.0], group=group, is_baseline=True)
    candidate = _completed_run(context, dataset, "Mini", [1.0, 1.0], group=group)

    output = _call("compare_evaluations", {"run": str(candidate.id)}, context).structuredContent

    assert output["overall"]["status"] == "improved"
    assert output["overall"]["delta"] == pytest.approx(0.25)
    assert (output["paired"], output["wins"], output["losses"], output["ties"]) == (2, 1, 0, 1)
    alone = _completed_run(context, dataset, "Alone", [1.0], group=uuid.uuid4())
    missing = _call("compare_evaluations", {"run": str(alone.id)}, context)
    assert missing.structuredContent["error"]["code"] == "evaluation_not_ready"


def test_native_workshop_dataset_rejects_chat_generation_in_rest_and_mcp():
    from conftest import import_version
    from factories import auth_client

    from overbae.services.datasets import land
    from overbae.services.datasets.examples import prepare_examples

    context = _writer()
    dataset = Dataset.objects.create(project=context.project, name="Native", intent="eval")
    land.land_rows(
        dataset,
        [{"state": "", "question": "Choose", "kind": "noul", "options": [], "target": [0.7]}],
    )
    prepared = prepare_examples(
        store.read_frame(paths.cell_path(dataset.pk, dataset.source.pk)), "eval"
    )
    import_version(dataset, prepared.to_dict(orient="records"))
    dataset.refresh_from_db()
    cell = dataset.active_cell
    assert cell.intent_report["eval"]["ok"]
    make_matrix_row(context.project, "Match", type="deterministic", source=EXACT_MATCH)
    payload = {
        "dataset": str(dataset.pk),
        "cell": str(cell.pk),
        "targets": [{"mode": "generate", "model_name": "gpt-4.1"}],
    }
    readiness = _call("check_evaluation_readiness", payload, context).structuredContent
    assert not readiness["ready"]
    assert not readiness["dataset"]["cell"]["fits"]
    assert "native probability" in readiness["dataset"]["cell"]["reason"]
    rejected = _call("run_evaluation", {**payload, "name": "Native"}, context)
    assert rejected.isError
    assert rejected.structuredContent["error"]["code"] == "evaluation_not_ready"
    response = auth_client(context.user).post(
        "/api/eval-runs/",
        {**payload, "name": "Native", "project": str(context.project.pk)},
        format="json",
    )
    assert response.status_code == 400, response.data
    assert "native probability" in str(response.data)
    with pytest.raises(runs.RunError, match="native probability"):
        runs.create_run(
            cell=cell, mode="generate", matrix=runs.freeze_matrix(project=context.project)
        )
    assert not EvalRun.objects.filter(dataset=dataset).exists()
