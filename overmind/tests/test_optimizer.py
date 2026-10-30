"""No-network checks for the client-driven optimiser loop."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from overmind.optimizer import OptimiseLoop
from overmind.optimizer_api import (
    _run_datapoint,
)

pytestmark = pytest.mark.covers("state-machines")


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


@pytest.mark.covers("lineage")
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


@pytest.mark.covers("lineage")
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


@pytest.mark.covers("lineage")
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
