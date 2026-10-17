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
    control.add_consumer.return_value = [{"mixed": {"ok": "started"}}]
    monkeypatch.setattr(worker_drain.app, "control", control)
    monkeypatch.setattr(worker_drain.time, "sleep", lambda _: None)
    worker_drain.drain_mixed_io()
    assert control.cancel_consumer.call_count == 2
    assert all(
        call.kwargs["destination"] == ["mixed"] for call in control.cancel_consumer.call_args_list
    )
    control.add_consumer.assert_called_once_with("io", destination=["mixed"], reply=True, timeout=5)


def test_missing_worker_reply_aborts_and_restores_consumption(monkeypatch):
    initial = Mock()
    initial.active_queues.return_value = {"mixed": [{"name": "io"}, {"name": "io_traces"}]}
    missing = Mock()
    missing.active.return_value = None
    missing.reserved.return_value = None
    control = Mock()
    control.inspect.side_effect = [initial, missing]
    control.cancel_consumer.return_value = [{"mixed": {"ok": "stopped"}}]
    control.add_consumer.return_value = [{"mixed": {"ok": "started"}}]
    monkeypatch.setattr(worker_drain.app, "control", control)
    with pytest.raises(RuntimeError, match="disappeared"):
        worker_drain.drain_mixed_io()
    assert control.add_consumer.call_count == 2


@pytest.mark.parametrize("failure", ["register", "update"])
def test_failed_deployment_keeps_io_consuming(monkeypatch, failure):
    from scripts import deploy_ecs

    initial, drained = Mock(), Mock()
    initial.active_queues.return_value = {"mixed": [{"name": "io"}, {"name": "io_traces"}]}
    drained.active.return_value = {"mixed": []}
    drained.reserved.return_value = {"mixed": []}
    control = Mock()
    control.inspect.side_effect = [initial, drained]
    control.cancel_consumer.return_value = [{"mixed": {"ok": "stopped"}}]
    control.add_consumer.return_value = [{"mixed": {"ok": "started"}}]
    monkeypatch.setattr(worker_drain.app, "control", control)
    monkeypatch.setattr(deploy_ecs, "drain_io", lambda *_: worker_drain.drain_mixed_io())
    monkeypatch.setattr(deploy_ecs, "describe_service", lambda *_: {"desiredCount": 1})

    def register(*_):
        if failure == "register":
            raise RuntimeError("registration failed")
        return {}, {"taskDefinitionArn": "new:1"}

    def unavailable(*_, **__):
        raise RuntimeError("update failed")

    monkeypatch.setattr(deploy_ecs, "register", register)
    monkeypatch.setattr(deploy_ecs, "aws", unavailable)
    with pytest.raises(RuntimeError, match="failed"):
        deploy_ecs.deploy("test", "celery-io-worker", "repo:new")
    if failure == "register":
        control.cancel_consumer.assert_not_called()
    else:
        control.add_consumer.assert_called_once_with(
            "io", destination=["mixed"], reply=True, timeout=5
        )


def test_unacknowledged_io_restoration_fails_the_drain(monkeypatch):
    inspector = Mock()
    inspector.active_queues.return_value = {"mixed": [{"name": "io"}, {"name": "io_traces"}]}
    inspector.active.return_value = {"mixed": []}
    inspector.reserved.return_value = {"mixed": []}
    control = Mock()
    control.inspect.return_value = inspector
    control.cancel_consumer.return_value = [{"mixed": {"ok": "stopped"}}]
    control.add_consumer.return_value = []
    monkeypatch.setattr(worker_drain.app, "control", control)
    with pytest.raises(RuntimeError, match="restoration was not acknowledged"):
        worker_drain.drain_mixed_io()
