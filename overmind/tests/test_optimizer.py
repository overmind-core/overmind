"""No-network checks for the client-driven optimiser loop."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from overmind.optimizer import OptimiseLoop, command_template_prompt
from overmind.optimizer_api import (
    TOKENS,
    _new_traceparent,
    _render_command,
    _run_datapoint,
)


def test_traceparent_shape():
    header, trace_id = _new_traceparent()
    assert header == f"00-{trace_id}-{header.split('-')[2]}-01"
    assert len(trace_id) == 32 and int(trace_id, 16) >= 0


def test_render_command_substitutes_tokens():
    rendered = _render_command(
        "echo __DATAPOINT_INPUT__ __CANDIDATE_ID__ __DATAPOINT_INDEX__",
        experiment_id="e1",
        capability_id="c1",
        project_id="p1",
        candidate_id="cand",
        iteration_id="it",
        datapoint_index=3,
        trace_type="candidate",
        original_trace_id="",
        datapoint_input={"q": 1},
    )
    assert TOKENS["DATAPOINT_INPUT"] not in rendered
    assert "{'q': 1}" in rendered
    assert "cand" in rendered
    assert rendered.endswith("3") or " 3" in rendered


def test_run_datapoint_success_round_trip(tmp_path):
    result = _run_datapoint(
        template="echo hi",
        experiment_id="e",
        capability_id="c",
        project_id="p",
        candidate_id="cand",
        iteration_id="it",
        datapoint_index=0,
        datapoint_input="x",
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result["success"] is True
    assert result["output"].strip() == "hi"
    assert len(result["trace_id"]) == 32


def test_run_datapoint_failure_reports_error(tmp_path):
    result = _run_datapoint(
        template="echo boom >&2; exit 3",
        experiment_id="e",
        capability_id="c",
        project_id="p",
        candidate_id="cand",
        iteration_id="it",
        datapoint_index=0,
        datapoint_input="x",
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result["success"] is False
    assert "boom" in (result.get("error") or "")


def test_export_dataset_reuses_a_cache_whose_hash_matches_the_pin(tmp_path):
    from overmind.optimizer_api import OptimizerAPI

    cache = tmp_path / "datasets"
    cache.mkdir()
    (cache / "ver-9.jsonl").write_text('{"input": 1}\n')
    (cache / "ver-9.hash").write_text("abc")
    api = OptimizerAPI("http://unused", "k")
    api._session = MagicMock()
    assert api.export_dataset("ds-1", "ver-9", cache, fingerprint="abc") == cache / "ver-9.jsonl"
    assert api.export_dataset("ds-1", "ver-9", cache) == cache / "ver-9.jsonl"
    api._session.get.assert_not_called()


def test_export_dataset_fetches_the_pinned_checkpoint_and_keeps_its_hash(tmp_path):
    from overmind.optimizer_api import OptimizerAPI

    api = OptimizerAPI("http://api", "k")
    api._session = MagicMock()
    api._session.get.return_value.iter_content.return_value = [b'{"input": 1}\n']
    api._session.get.return_value.headers = {"X-Overmind-Fingerprint": "abc"}
    cache = tmp_path / "datasets"
    path = api.export_dataset("ds-1", "ver-9", cache)
    assert path == cache / "ver-9.jsonl"
    assert path.read_text() == '{"input": 1}\n'
    assert (cache / "ver-9.hash").read_text() == "abc"
    call = api._session.get.call_args
    assert call.args[0] == "http://api/api/datasets/ds-1/export/"
    assert call.kwargs["params"] == {"fmt": "jsonl", "cell": "ver-9"}


def test_export_dataset_refetches_when_the_hash_is_stale_or_missing(tmp_path):
    from overmind.optimizer_api import OptimizerAPI

    cache = tmp_path / "datasets"
    cache.mkdir()
    (cache / "ver-9.jsonl").write_text("old\n")
    (cache / "ver-9.hash").write_text("old-hash")
    api = OptimizerAPI("http://api", "k")
    api._session = MagicMock()
    api._session.get.return_value.iter_content.return_value = [b"new\n"]
    api._session.get.return_value.headers = {"X-Overmind-Fingerprint": "new-hash"}
    api.export_dataset("ds-1", "ver-9", cache, fingerprint="new-hash")
    assert (cache / "ver-9.jsonl").read_text() == "new\n"
    assert (cache / "ver-9.hash").read_text() == "new-hash"
    (cache / "ver-9.hash").unlink()
    api.export_dataset("ds-1", "ver-9", cache)
    assert api._session.get.call_count == 2


def test_export_dataset_needs_a_used_version(tmp_path):
    from overmind.optimizer_api import OptimizerAPI

    api = OptimizerAPI("http://api", "k")
    with pytest.raises(RuntimeError, match="used version"):
        api.export_dataset("ds-1", "", tmp_path)


def test_next_action_asks_for_template(tmp_path):
    api = MagicMock()
    api.get_experiment.return_value = {
        "id": "e1",
        "status": "scheduled",
        "command_template": "",
        "iterations": [],
        "capability_name": "picker",
        "entrypoint": "run",
    }
    dataset = tmp_path / "ds.jsonl"
    dataset.write_text("{}\n")
    loop = OptimiseLoop(
        api,
        "e1",
        repo_cwd=str(tmp_path),
        dataset_path=dataset,
        state_path=tmp_path / "state.json",
    )
    action = loop.next_action()
    assert action["action"] == "WRITE_COMMAND_TEMPLATE"
    assert "set-template" in action["prompt"]


def _loop_with_experiment(tmp_path, exp: dict) -> OptimiseLoop:
    api = MagicMock()
    api.get_experiment.return_value = exp
    dataset = tmp_path / "ds.jsonl"
    dataset.write_text("{}\n")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"smoke_done": True, "pending_diffs": []}))
    return OptimiseLoop(
        api,
        "e1",
        repo_cwd=str(tmp_path),
        dataset_path=dataset,
        state_path=state_path,
    )


def test_next_action_completes_on_plateau(tmp_path):
    loop = _loop_with_experiment(
        tmp_path,
        {
            "id": "e1",
            "status": "evaluated_candidate_outputs",
            "command_template": "run.sh",
            "iterations": [{"order": 0}, {"order": 1}, {"order": 2}],
            "current_iteration": 2,
            "num_iterations": 5,
            "stalled_iterations": 3,
            "max_iterations_without_improvement": 3,
            "scores": {"baseline": 70.0, "best": 70.5},
        },
    )
    action = loop.next_action()
    assert action["action"] == "COMPLETE"
    assert "plateau" in action["reason"]


def test_next_action_keeps_iterating_below_plateau(tmp_path):
    loop = _loop_with_experiment(
        tmp_path,
        {
            "id": "e1",
            "status": "evaluated_candidate_outputs",
            "command_template": "run.sh",
            "iterations": [{"order": 0}, {"order": 1}],
            "current_iteration": 1,
            "num_iterations": 5,
            "stalled_iterations": 1,
            "max_iterations_without_improvement": 3,
            "scores": {"baseline": 70.0, "best": 80.0},
        },
    )
    action = loop.next_action()
    assert action["action"] == "WRITE_CANDIDATES"


def test_command_template_prompt_keeps_tokens():
    prompt = command_template_prompt(capability_name="picker")
    assert "__DATAPOINT_INPUT__" in prompt
    assert "overmind optimise set-template" in prompt


def test_add_candidate_diff_queues_locally(tmp_path):
    api = MagicMock()
    dataset = tmp_path / "ds.jsonl"
    dataset.write_text("{}\n")
    loop = OptimiseLoop(
        api,
        "e1",
        repo_cwd=str(tmp_path),
        dataset_path=dataset,
        state_path=tmp_path / "state.json",
    )
    result = loop.add_candidate_diff("diff --git a/x b/x\n")
    assert result["pending"] == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert len(state["pending_diffs"]) == 1
