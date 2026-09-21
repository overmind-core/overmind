import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from experiments.inference_upgrade import multigpu_snapshot_probe as probe
from experiments.inference_upgrade.multigpu_snapshot_probe import validate_results, wait_results


def rows():
    return [
        {
            "rank": rank,
            "origin": "origin",
            "phase": "restored",
            "request": "runtime",
            "elements": 1024,
            "value": 75,
            "device": f"cuda:{rank}",
            "data_ptr": 1024 + rank,
        }
        for rank in range(2)
    ]


def test_rank_validation_accepts_only_matching_complete_results():
    expected = rows()
    validate_results(expected, "origin", "restored", "runtime", 75)
    for field, value in (
        ("rank", 1),
        ("origin", "stale"),
        ("phase", "ready"),
        ("request", "stale"),
        ("elements", 0),
        ("value", 3),
        ("device", "cuda:1"),
        ("data_ptr", 0),
    ):
        changed = deepcopy(expected)
        changed[0][field] = value
        with pytest.raises(RuntimeError):
            validate_results(changed, "origin", "restored", "runtime", 75)
    with pytest.raises(RuntimeError):
        validate_results(expected[:1], "origin", "restored", "runtime", 75)


def test_result_wait_reads_atomic_rank_files_and_detects_failed_child(tmp_path):
    expected = rows()
    processes = [Mock(poll=Mock(return_value=None)), Mock(poll=Mock(return_value=None))]
    for row in expected:
        (tmp_path / f"restored-{row['rank']}.json").write_text(json.dumps(row))
    assert wait_results(tmp_path, processes, "restored") == expected
    processes[0].poll.return_value = 1
    processes[0].returncode = 1
    with pytest.raises(RuntimeError, match="child exited before ready"):
        wait_results(tmp_path, processes, "ready")
    processes[0].poll.return_value = None
    with pytest.raises(TimeoutError, match="did not finish ready"):
        wait_results(tmp_path, processes, "ready", timeout=0)


@pytest.mark.parametrize("changed_address", [False, True])
def test_probe_validates_restored_addresses_and_always_schedules_shutdown(
    monkeypatch, tmp_path, changed_address
):
    instance = SimpleNamespace(
        origin="origin",
        runtime="abcd-runtime",
        directory=tmp_path,
        processes=[],
        before=rows(),
    )
    after = rows()
    for row in after:
        row["request"] = instance.runtime
        row["value"] = 2 * (17 + int("abcd", 16) % 100) + 1
    if changed_address:
        after[0]["data_ptr"] += 1
    monkeypatch.setattr(probe, "wait_results", Mock(return_value=after))
    timer = Mock()
    monkeypatch.setattr(probe.threading, "Timer", timer)
    run = probe.Probe._get_user_cls().probe._get_raw_f()
    if changed_address:
        with pytest.raises(RuntimeError, match="CUDA parameter addresses"):
            run(instance)
    else:
        result = run(instance)
        assert result["nccl_verified"] is True
        assert result["origin"] == instance.origin
        assert result["runtime"] == instance.runtime
    command = json.loads((tmp_path / "command.json").read_text())
    assert command == {"request": instance.runtime, "factor": 17 + int("abcd", 16) % 100}
    assert not (tmp_path / "command.tmp").exists()
    timer.return_value.start.assert_called_once()
