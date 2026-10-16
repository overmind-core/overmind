from unittest.mock import Mock

import pytest

from overbae import worker_drain


def test_mixed_consumers_stop_fetching_and_drain_reserved_work(monkeypatch):
    initial = Mock()
    initial.active_queues.return_value = {
        "mixed": [{"name": "io"}, {"name": "io_traces"}],
        "isolated": [{"name": "io_traces"}],
    }
    draining = Mock()
    draining.active.side_effect = [{"mixed": [{"name": "working"}]}, {"mixed": []}]
    draining.reserved.side_effect = [{"mixed": [{"name": "reserved"}]}, {"mixed": []}]
    control = Mock()
    control.inspect.side_effect = [initial, draining, draining]
    control.cancel_consumer.return_value = [{"mixed": {"ok": "stopped"}}]
    monkeypatch.setattr(worker_drain.app, "control", control)
    monkeypatch.setattr(worker_drain.time, "sleep", lambda _: None)
    worker_drain.drain_mixed_io()
    assert control.cancel_consumer.call_count == 2
    assert all(
        call.kwargs["destination"] == ["mixed"] for call in control.cancel_consumer.call_args_list
    )
    control.add_consumer.assert_not_called()


def test_missing_worker_reply_aborts_and_restores_consumption(monkeypatch):
    initial = Mock()
    initial.active_queues.return_value = {"mixed": [{"name": "io"}, {"name": "io_traces"}]}
    missing = Mock()
    missing.active.return_value = None
    missing.reserved.return_value = None
    control = Mock()
    control.inspect.side_effect = [initial, missing]
    control.cancel_consumer.return_value = [{"mixed": {"ok": "stopped"}}]
    monkeypatch.setattr(worker_drain.app, "control", control)
    with pytest.raises(RuntimeError, match="disappeared"):
        worker_drain.drain_mixed_io()
    assert control.add_consumer.call_count == 2
