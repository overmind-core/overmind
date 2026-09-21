from unittest.mock import Mock

import pytest

from scripts import validate_lora_snapshots as canary


def test_control_wakes_weights_then_kv_before_inference(monkeypatch):
    worker = Mock()
    worker.infer.remote.side_effect = [
        {"status": 200, "body": b""},
        {"status": 200, "body": b'{"is_sleeping": true}'},
        {"status": 200, "body": b""},
        {"status": 200, "body": b'{"is_sleeping": true}'},
        {"status": 200, "body": b""},
        {"status": 200, "body": b'{"is_sleeping": false}'},
        {"status": 200, "body": b""},
    ]
    request = Mock(return_value={"content": "391"})
    monkeypatch.setattr(canary, "request", request)
    canary.wake_control(worker, {}, cycles=1)
    assert [call.kwargs["path"] for call in worker.infer.remote.call_args_list] == [
        "/sleep?level=1",
        "/is_sleeping",
        "/wake_up?tags=weights",
        "/is_sleeping",
        "/wake_up?tags=kv_cache",
        "/is_sleeping",
        "/health",
    ]
    assert request.call_count == 2
    assert all(call.args[2] is None for call in request.call_args_list)


@pytest.mark.parametrize("status,body", [(500, b"wake failed"), (200, b'{"is_sleeping": false}')])
def test_control_stops_on_failed_sleep_or_invalid_state(monkeypatch, status, body):
    worker = Mock()
    worker.infer.remote.return_value = {"status": status, "body": body}
    request = Mock(return_value={})
    monkeypatch.setattr(canary, "request", request)
    with pytest.raises(RuntimeError):
        canary.wake_control(worker, {}, cycles=1)
    assert request.call_count == 1
    assert not any("wake_up" in call.kwargs["path"] for call in worker.infer.remote.call_args_list)
