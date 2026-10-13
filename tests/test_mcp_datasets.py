from __future__ import annotations

import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from conftest import frozen_dataset, read_table
from django.db import close_old_connections
from django.utils import timezone
from mcp_fixtures import mcp_context

from overbae.models import Capability, Cell, Dataset, Execution, Project, Span
from overbae.services.datasets import executions, land, paths
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)

ROWS = [
    {"input": "q1", "expected_output": "a1", "tag": "keep"},
    {"input": "", "expected_output": "orphan", "tag": "junk"},
    {"input": "q3", "expected_output": "a3", "tag": "keep"},
]
KEEP = "df = df[df['tag'] == 'keep']\n"


def _call(name: str, arguments: dict, context: MCPContext):
    return asyncio.run(CATALOG.call(name, arguments, context))


def _dataset(context: MCPContext, name: str = "Eval", **kwargs) -> Dataset:
    defaults = {
        "project": context.project,
        "name": name,
        "intent": Dataset.Intent.EVAL,
        "source_kind": Dataset.SourceKind.TRACES,
        "state": Dataset.State.IDLE,
    }
    defaults.update(kwargs)
    return Dataset.objects.create(**defaults)


def _ran_cell(dataset: Dataset, *, position: int = 0, title: str = "Source", parent=None) -> Cell:
    return Cell.objects.create(
        dataset=dataset,
        parent=parent,
        script_sha256=f"{position:064x}" if parent else "",
        position=position,
        title=title,
        rows=3,
        fingerprint="f" * 64,
        columns=[{"name": "input", "type": "string"}],
        intent_report={"eval": {"ok": True, "reason": ""}},
        capability_report={"ok": True},
    )


def _landed(context: MCPContext, rows=ROWS) -> Dataset:
    return frozen_dataset(context.project, rows, contract="eval")


def test_try_previews_a_script_without_creating_a_version():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    result = _call(
        "try_dataset_script",
        {"dataset": str(dataset.id), "script": KEEP + "print(len(df))"},
        context,
    )
    assert result.isError is False, result.structuredContent
    body = result.structuredContent
    assert (body["status"], body["rows"], body["stdout"]) == ("ok", 2, "2\n")
    assert body["columns"] == ["input", "expected_output", "tag"]
    assert [row["input"] for row in body["head"]] == ["q1", "q3"]
    assert dataset.cells.count() == 1
    assert Execution.objects.get(pk=body["execution"]).mode == Execution.Mode.TRY


def test_a_try_previews_missing_values_as_null():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    script = (
        "df['score'] = [1.5, float('nan'), None]\n"
        "df['match'] = df['input'].map(lambda s: s if s else None)\n"
        "df['at'] = pd.to_datetime(['2026-01-02', None, '2026-01-03'])\n"
    )
    body = _call(
        "try_dataset_script", {"dataset": str(dataset.id), "script": script}, context
    ).structuredContent
    assert body["status"] == "ok", body
    assert [row["score"] for row in body["head"]] == [1.5, None, None]
    assert [row["match"] for row in body["head"]] == ["q1", None, "q3"]
    assert body["head"][0]["at"].startswith("2026-01-02") and body["head"][1]["at"] is None


def test_probe_returns_what_the_script_prints():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    result = _call(
        "probe_dataset_version",
        {
            "dataset": str(dataset.id),
            "version": "v0",
            "script": "print(df['tag'].value_counts().to_dict())",
        },
        context,
    )
    assert result.structuredContent["status"] == "ok"
    assert result.structuredContent["stdout"] == "{'keep': 2, 'junk': 1}\n"


def test_refused_synthetic_rows_say_the_rows_failed_not_a_script():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    result = _call(
        "add_synthetic_examples",
        {
            "dataset": str(dataset.id),
            "parent": "v0",
            "rows": [{"tag": "keep"}],
            "instruction": "Rows with no input",
        },
        context,
    ).structuredContent
    assert (result["status"], result["summary"]) == ("failed", "The rows were refused.")
    assert "do not fit the eval intent" in result["error"]


def test_an_added_version_is_measured_inactive_and_activated_only_on_request():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    added = _call(
        "add_dataset_version",
        {"dataset": str(dataset.id), "parent": "v0", "title": "Keep", "script": KEEP},
        context,
    )
    assert added.isError is False, added.structuredContent
    version = added.structuredContent["version"]
    assert (version["version"], version["parent"], version["rows"]) == ("v1", "v0", 2)
    assert version["impact"]["rows_removed"] == 1 and version["used"] is False
    assert added.structuredContent["summary"] == "v1 from v0: 2 rows, not active."
    inspected = _call("inspect_dataset", {"dataset": str(dataset.id)}, context).structuredContent
    assert inspected["active"]["version"] == "v0"
    assert [v["version"] for v in inspected["versions"]] == ["v0", "v1"]

    stale = _call(
        "activate_dataset_version",
        {"dataset": str(dataset.id), "version": "v1", "expected_active": "v1"},
        context,
    )
    assert stale.structuredContent["error"]["code"] == "active_conflict"
    assert stale.structuredContent["error"]["fields"] == {}
    activated = _call(
        "activate_dataset_version",
        {"dataset": str(dataset.id), "version": "v1", "expected_active": "v0"},
        context,
    )
    assert activated.isError is False, activated.structuredContent
    assert activated.structuredContent["dataset"]["active"]["version"] == "v1"
    diff = _call("diff_dataset_versions", {"dataset": str(dataset.id)}, context)
    assert (diff.structuredContent["from"], diff.structuredContent["to"]) == ("v0", "v1")
    assert diff.structuredContent["diff"]["rows_removed"] == 1


def test_the_same_parent_and_script_return_one_version_even_when_sent_at_once():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)

    def add(title):
        close_old_connections()
        try:
            result = _call(
                "add_dataset_version",
                {"dataset": str(dataset.id), "title": title, "script": KEEP},
                context,
            )
            return result.structuredContent["version"]["id"]
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(add, ["One", "Two", "Three"]))
    assert len(set(ids)) == 1
    assert dataset.cells.count() == 2
    again = _call(
        "add_dataset_version",
        {"dataset": str(dataset.id), "parent": "v0", "title": "Renamed", "script": KEEP},
        context,
    ).structuredContent
    assert again["version"]["id"] == ids[0] and again["execution"] is None


def test_branches_are_siblings_under_one_parent():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    for script in (KEEP, "df = df[df['tag'] == 'junk']\n"):
        _call(
            "add_dataset_version",
            {"dataset": str(dataset.id), "parent": "v0", "title": "Branch", "script": script},
            context,
        )
    versions = _call("inspect_dataset", {"dataset": str(dataset.id)}, context).structuredContent[
        "versions"
    ]
    assert [(v["version"], v["parent"]) for v in versions] == [
        ("v0", None),
        ("v1", "v0"),
        ("v2", "v0"),
    ]


def test_a_failing_script_creates_no_version_and_its_failure_is_reused_for_an_hour():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    arguments = {"dataset": str(dataset.id), "title": "Broken", "script": "df = df['missing']\n"}
    failed = _call("add_dataset_version", arguments, context).structuredContent
    assert failed["status"] == "failed" and failed["transient"] is False
    assert "missing" in failed["error"] and failed["version"] is None
    assert dataset.cells.count() == 1
    assert (
        _call("add_dataset_version", arguments, context).structuredContent["execution"]
        == (failed["execution"])
    )
    Execution.objects.filter(pk=failed["execution"]).update(
        finished_at=timezone.now() - timedelta(hours=1, minutes=1)
    )
    later = _call("add_dataset_version", arguments, context).structuredContent
    assert later["status"] == "failed" and later["execution"] != failed["execution"]


def test_an_unreachable_sandbox_fails_transiently_and_the_same_call_runs_again(settings):
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    working = settings.SANDBOX_URL
    settings.SANDBOX_URL = "http://127.0.0.1:9"
    arguments = {"dataset": str(dataset.id), "script": KEEP}
    down = _call("try_dataset_script", arguments, context).structuredContent
    assert (down["status"], down["transient"], down["error"]) == (
        "failed",
        True,
        "Scripts cannot run right now.",
    )
    settings.SANDBOX_URL = working
    back = _call("try_dataset_script", arguments, context).structuredContent
    assert back["status"] == "ok" and back["execution"] != down["execution"]


def test_a_sandbox_that_refuses_the_token_fails_the_run_without_blaming_the_script(settings):
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    settings.SANDBOX_TOKEN = "wrong"
    refused = _call(
        "try_dataset_script", {"dataset": str(dataset.id), "script": KEEP}, context
    ).structuredContent
    assert (refused["status"], refused["transient"], refused["error"]) == (
        "failed",
        True,
        "The run could not start. Run it again later.",
    )


def test_a_try_on_a_wide_frame_previews_the_first_200_columns():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    script = "for i in range(250):\n    df[f'c{i}'] = i"
    tried = _call(
        "try_dataset_script", {"dataset": str(dataset.id), "script": script}, context
    ).structuredContent
    assert tried["status"] == "ok", tried["error"]
    assert len(tried["columns"]) == 200


def test_a_run_that_stopped_without_a_result_is_failed_and_runs_again():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    script = KEEP
    stuck = Execution.objects.create(
        key=executions.key("try", dataset.id, dataset.source.id, script),
        project=context.project,
        dataset=dataset,
        parent=dataset.source,
        mode="try",
        script=script,
        state="running",
        queued_at=timezone.now() - timedelta(minutes=13),
        started_at=timezone.now() - timedelta(minutes=12),
    )
    rerun = _call("try_dataset_script", {"dataset": str(dataset.id), "script": script}, context)
    assert rerun.structuredContent["status"] == "ok"
    stuck.refresh_from_db()
    assert (stuck.state, stuck.transient, stuck.error) == (
        "failed",
        True,
        "The run stopped without a result.",
    )


def test_the_sweep_fails_stuck_runs_and_deletes_runs_after_thirty_days():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    now = timezone.now()

    def run(**fields):
        return Execution.objects.create(
            key=uuid.uuid4().hex,
            project=context.project,
            dataset=dataset,
            parent=dataset.source,
            mode="try",
            **fields,
        )

    stuck = run(
        state="running",
        queued_at=now - timedelta(minutes=12),
        started_at=now - timedelta(minutes=12),
    )
    lost = run(state="queued", queued_at=now - timedelta(minutes=21))
    fresh = run(state="running", queued_at=now, started_at=now)
    old = run(state="ok", queued_at=now - timedelta(days=31), finished_at=now - timedelta(days=31))
    assert executions.sweep() == {"stopped": 2, "deleted": 1}
    for item in (stuck, lost, fresh):
        item.refresh_from_db()
    assert (stuck.state, lost.state, fresh.state) == ("failed", "failed", "running")
    assert stuck.transient and lost.transient
    assert not Execution.objects.filter(pk=old.pk).exists()


def test_every_run_has_a_row_with_timings():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    _call(
        "add_dataset_version",
        {"dataset": str(dataset.id), "title": "Keep", "script": KEEP},
        context,
    )
    run = dataset.executions.get()
    assert run.state == "ok" and run.cell_id is not None
    assert None not in (run.started_at, run.finished_at, run.queue_seconds, run.run_seconds)
    assert run.transfer_seconds is not None and run.stdout == ""


def test_scripts_and_versions_are_refused_while_landing_and_unknown_versions_name_the_field():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    missing = _call(
        "add_dataset_version",
        {"dataset": str(dataset.id), "parent": "v9", "title": "T", "script": KEEP},
        context,
    ).structuredContent["error"]
    assert missing["code"] == "cell_not_found" and missing["fields"] == {"parent": "No version v9."}
    Dataset.objects.filter(pk=dataset.pk).update(state="landing")
    busy = _call("try_dataset_script", {"dataset": str(dataset.id), "script": KEEP}, context)
    assert busy.structuredContent["error"]["code"] == "dataset_busy"
    assert "dataset_import" in busy.structuredContent["error"]["message"]


def test_update_dataset_renames_sets_intent_and_detaches_the_capability():
    context = mcp_context(("read", "write"))
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    dataset = _landed(context)
    set_capability = _call(
        "update_dataset",
        {"dataset": str(dataset.id), "name": "Renamed", "capability": str(capability.id)},
        context,
    ).structuredContent
    assert set_capability["dataset"]["name"] == "Renamed"
    assert set_capability["dataset"]["capability"]["id"] == str(capability.id)
    detached = _call(
        "update_dataset",
        {"dataset": str(dataset.id), "capability": None, "intent": "train"},
        context,
    ).structuredContent
    assert detached["dataset"]["capability"] is None and detached["dataset"]["intent"] == "train"
    empty = _call("update_dataset", {"dataset": str(dataset.id)}, context)
    assert empty.structuredContent["error"]["code"] == "invalid_input"


def test_query_stops_a_slow_query_and_refuses_a_large_result():
    context = mcp_context(("read", "write"))
    dataset = _landed(context)
    slow = _call(
        "query_dataset",
        {"dataset": str(dataset.id), "sql": "select count(*) from range(100000000000)"},
        context,
    )
    assert slow.structuredContent["error"]["code"] == "query_timeout"
    large = _call(
        "query_dataset",
        {"dataset": str(dataset.id), "sql": "select repeat('x', 20000) as a from range(3)"},
        context,
    )
    assert large.structuredContent["error"]["code"] == "query_result_too_large"


def test_list_datasets_is_project_scoped_filtered_paginated_and_uses_uuids():
    context = mcp_context(("read", "write"))
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    first = _dataset(context, "Alpha", capability=capability)
    _dataset(context, "Beta", intent=Dataset.Intent.TRAIN)
    other = Project.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    Dataset.objects.create(project=other, name="Alpha foreign")

    result = _call(
        "list_datasets",
        {"capability": str(capability.id), "intent": "eval", "search": "Alpha", "limit": 1},
        context,
    )

    assert result.isError is False
    assert [item["id"] for item in result.structuredContent["datasets"]] == [str(first.id)]
    assert result.structuredContent["page"] == {
        "limit": 1,
        "offset": 0,
        "total": 1,
        "has_more": False,
        "next_cursor": None,
    }
    uuid.UUID(result.structuredContent["datasets"][0]["id"])


def test_inspection_exposes_the_workshops_source_families_and_consumer_requirements():
    context = mcp_context(("read", "write"))
    dataset = _dataset(context)
    land.land_rows(
        dataset,
        [
            {
                "input": {
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "evidence"},
                    ]
                },
                "expected_output": {"answer": "supported"},
            }
            for prompt in ["Extract fields"] * 25 + ["Apply rules"] * 5
        ],
    )
    result = _call("inspect_dataset", {"dataset": str(dataset.id)}, context)
    assert not result.isError
    preparation = result.structuredContent["preparation_context"]
    assert preparation["profiles"]["source"]["rows_scanned"] == 30
    assert [family["rows"] for family in preparation["profiles"]["source"]["families"]] == [25, 5]
    assert "not the application" in preparation["consumers"]["model_evaluation"]["execution"]


def test_inspect_is_bounded_ordered_and_refuses_an_ambiguous_name():
    context = mcp_context(("read", "write"))
    dataset = _dataset(
        context,
        "Named dataset",
        capability_rank=[
            {"capability_id": str(uuid.uuid4()), "name": f"Capability {n}", "score": n}
            for n in range(25)
        ],
    )
    active = _ran_cell(dataset, position=0)
    dataset.active = active
    dataset.save(update_fields=["active"])
    for position in range(1, 4):
        _ran_cell(dataset, position=position, title=f"Step {position}", parent=active)
    result = _call(
        "inspect_dataset", {"dataset": str(dataset.id), "cell_offset": 1, "cell_limit": 2}, context
    )
    by_name = _call("inspect_dataset", {"dataset": dataset.name}, context)

    assert result.isError is False
    body = result.structuredContent
    assert [v["version"] for v in body["versions"]] == ["v1", "v2"]
    assert body["versions_total"] == 4
    assert body["active"]["id"] == str(active.id)
    assert len(body["capability_rank"]) == 20
    assert body["next_actions"][0]["tool"] == "check_evaluation_readiness"
    assert by_name.isError is False
    assert by_name.structuredContent["id"] == str(dataset.id)
    Dataset.objects.create(project=context.project, name=dataset.name)
    twice = _call("inspect_dataset", {"dataset": dataset.name}, context)
    assert twice.structuredContent["error"]["code"] == "dataset_not_found"
    assert "use the dataset id" in twice.structuredContent["error"]["message"]


@pytest.mark.parametrize(
    ("state", "tools"),
    [(Dataset.State.LANDING, ["get_job"]), (Dataset.State.ERROR, [])],
)
def test_inspect_next_action_follows_dataset_state(state: str, tools: list[str]):
    context = mcp_context(("read", "write"))
    dataset = _dataset(context, state=state, error="/private/tmp/secret.parquet")

    result = _call("inspect_dataset", {"dataset": str(dataset.id)}, context)

    assert [action["tool"] for action in result.structuredContent["next_actions"]] == tools
    assert "/private/tmp" not in json.dumps(result.structuredContent)


def test_query_is_project_and_cell_scoped_read_only_and_capped():
    context = mcp_context(("read", "write"))
    dataset = frozen_dataset(
        context.project, [{"input": str(n), "expected_output": "x"} for n in range(150)]
    )
    foreign_dataset = _dataset(context, "Foreign dataset")
    foreign_cell = _ran_cell(foreign_dataset)
    ok = _call(
        "query_dataset",
        {"dataset": str(dataset.id), "version": "v0", "sql": "select input from t"},
        context,
    )
    foreign = _call(
        "query_dataset",
        {"dataset": str(dataset.id), "version": str(foreign_cell.id), "sql": "select * from t"},
        context,
    )
    write = _call(
        "query_dataset",
        {"dataset": str(dataset.id), "sql": "delete from t"},
        context,
    )

    assert ok.isError is False
    assert len(ok.structuredContent["rows"]) == 100
    assert ok.structuredContent["columns"] == ["input"]
    assert ok.structuredContent["truncated"] is True
    assert foreign.structuredContent["error"]["code"] == "cell_not_found"
    assert write.structuredContent["error"]["code"] == "query_invalid"


def test_query_returns_aggregates_as_numbers():
    context = mcp_context(("read", "write"))
    dataset = frozen_dataset(
        context.project, [{"input": str(n), "expected_output": "x", "n": n} for n in range(4)]
    )
    totals = _call(
        "query_dataset",
        {"dataset": str(dataset.id), "sql": "select sum(n) total, avg(n) mean from t"},
        context,
    ).structuredContent["rows"]
    assert totals == [{"total": 6, "mean": 1.5}]


def _root_span(project, trace_id: str) -> Span:
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        project=project,
        span_type="entry_point",
        name="run",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        attributes={"overmind.input.data": trace_id, "overmind.output.data": "answer"},
    )


def test_trace_creation_refuses_unknown_filters_mixed_sources_and_empty_selections():
    context = mcp_context(("read", "write"))
    unknown = _call(
        "create_dataset_from_traces",
        {"name": "Trace set", "filters": {"capability_name": "x"}},
        context,
    )
    assert unknown.isError is True
    assert unknown.structuredContent["error"]["code"] == "invalid_input"
    assert "capability_name" in unknown.structuredContent["error"]["message"]
    mixed = _call(
        "create_dataset_from_traces",
        {"name": "Trace set", "trace_ids": ["b" * 32], "filters": {"has_error": "true"}},
        context,
    )
    assert mixed.isError is True
    empty = _call(
        "create_dataset_from_traces", {"name": "Trace set", "trace_ids": ["b" * 32]}, context
    )
    assert empty.isError is True
    assert empty.structuredContent["error"]["code"] == "no_traces"
    assert Dataset.objects.filter(project=context.project).count() == 0


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("choice", ["automatic", "none", "selected"])
def test_trace_creation_respects_the_capability_choice(split, choice):
    context = mcp_context(("read", "write"))
    matched = Capability.objects.create(project=context.project, name="Matched", slug="matched")
    selected = Capability.objects.create(project=context.project, name="Selected", slug="selected")
    trace_ids = [uuid.uuid4().hex for _ in range(4)]
    for trace_id in trace_ids:
        span = _root_span(context.project, trace_id)
        span.capability = matched
        span.save(update_fields=["capability"])
    arguments = {"name": "Choice", "trace_ids": trace_ids}
    if choice != "automatic":
        arguments["capability"] = None if choice == "none" else str(selected.id)
    if split:
        arguments["split"] = {"eval_percent": 30, "position": "tail"}
    result = _call("create_dataset_from_traces", arguments, context)
    assert result.isError is False, result.structuredContent
    body = result.structuredContent
    ids = [body["dataset"]["id"]]
    if split:
        ids.append(body["eval_dataset"]["id"])
    expected = {"automatic": matched.id, "none": None, "selected": selected.id}[choice]
    for dataset in Dataset.objects.filter(pk__in=ids):
        assert dataset.state == Dataset.State.IDLE, dataset.error
        assert dataset.capability_id == expected
        assert dataset.capability_rank[0]["capability_id"] == str(matched.id)


def test_trace_creation_returns_a_dataset_import_receipt():
    context = mcp_context(("read", "write"))
    Capability.objects.create(project=context.project, name="Support", slug="support")
    _root_span(context.project, "b" * 32)

    traces = _call(
        "create_dataset_from_traces",
        {"name": "Trace set", "trace_ids": ["b" * 32]},
        context,
    )
    for result in (traces,):
        body = result.structuredContent
        assert body["job"]["kind"] == "dataset_import"
        assert body["job"]["id"] == body["dataset"]["id"]
        assert body["job"]["resource"]["uri"] == (
            f"overmind://jobs/dataset_import/{body['dataset']['id']}"
        )
        assert body["dataset"]["resource"]["uri"] in {
            link["uri"] for link in body["resource_links"]
        }
        assert body["traces"] == 1
    dataset = Dataset.objects.get(pk=traces.structuredContent["dataset"]["id"])
    assert dataset.state == Dataset.State.IDLE, dataset.error
    assert dataset.active_cell.rows == 1
    job = _call("get_job", {"kind": "dataset_import", "id": str(dataset.id)}, context)
    completed = job.structuredContent["completed_at"]
    assert completed is not None
    assert datetime.fromisoformat(completed) >= dataset.source.created_at


def test_dataset_tool_text_is_complete_structured_json():
    context = mcp_context(("read", "write"))
    _dataset(context)

    result = _call("list_datasets", {}, context)

    assert json.loads(result.content[0].text) == result.structuredContent


def test_list_and_inspect_map_legacy_ft_intent():
    context = mcp_context(("read", "write"))
    dataset = _dataset(context, name="Old train", intent="ft")

    listed = _call("list_datasets", {}, context)
    assert listed.isError is False
    row = next(
        item for item in listed.structuredContent["datasets"] if item["id"] == str(dataset.id)
    )
    assert row["intent"] == "train"

    filtered = _call("list_datasets", {"intent": "train"}, context)
    assert filtered.isError is False
    assert str(dataset.id) in {item["id"] for item in filtered.structuredContent["datasets"]}

    inspected = _call("inspect_dataset", {"dataset": str(dataset.id)}, context)
    assert inspected.isError is False
    assert inspected.structuredContent["intent"] == "train"


def _llm_span(project, capability, text="hello"):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        project=project,
        capability=capability,
        span_type="llm_call",
        name="llm_call",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        status_code=0,
        attributes={
            "overmind.input.data": json.dumps([{"role": "user", "content": "question"}]),
            "overmind.output.data": json.dumps([{"role": "assistant", "content": text}]),
            "genai.model": "openai/gpt-5-mini",
        },
        usage={"genai.model": "openai/gpt-5-mini"},
    )


def test_llm_call_creation_refuses_an_empty_selection():
    context = mcp_context(("read", "write"))
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    result = _call(
        "create_dataset_from_llm_calls",
        {
            "name": "Calls",
            "capability": str(capability.id),
            "since": "2099-01-01T00:00:00+00:00",
        },
        context,
    )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "no_calls"
    assert Dataset.objects.filter(project=context.project).count() == 0


def test_llm_call_creation_lands_one_row_per_call():
    from django.utils import timezone

    context = mcp_context(("read", "write"))
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    _llm_span(context.project, capability, text="recorded")
    result = _call(
        "create_dataset_from_llm_calls",
        {
            "name": "Calls",
            "capability": capability.slug,
            "since": (timezone.now() - timedelta(hours=1)).isoformat(),
            "intent": "eval",
        },
        context,
    )
    assert result.isError is False, result.structuredContent
    assert result.structuredContent["calls"] == 1
    dataset = Dataset.objects.get(pk=result.structuredContent["dataset"]["id"])
    assert dataset.state == Dataset.State.IDLE, dataset.error
    assert dataset.intent == Dataset.Intent.EVAL
    assert dataset.source_kind == Dataset.SourceKind.LLM_CALLS
    frame = read_table(paths.cell_path(dataset.id, dataset.source.id))
    assert "trace_id" not in set(frame.columns)
    assert "span_id" in set(frame.columns)
    row = frame.iloc[0].to_dict()
    assert row["expected_output"]["content"] == "recorded"
    assert "trace_id" not in row
