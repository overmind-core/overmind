"""
Unit tests for the observe decorator.
"""

from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.trace import StatusCode

from overmind.tracing import observe


@pytest.fixture(autouse=True)
def reset_sdk_state():
    """Force-mark the SDK initialised so observe() doesn't call through.

    Tests here patch ``get_tracer`` and do not need a live SDK; the flag is
    restored so later tests on the same pytest-xdist worker keep the no-op
    tracer installed in ``tests/conftest.py``.
    """
    from overmind import tracing

    saved_initialized = tracing._initialized
    saved_tracer = tracing._tracer
    tracing._initialized = True
    yield
    tracing._initialized = saved_initialized
    tracing._tracer = saved_tracer


@pytest.fixture
def mock_tracer():
    """Create a mock tracer with span context manager."""
    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=False)

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value = mock_span

    return mock_tracer, mock_span


def test_observe_async(mock_tracer):
    import asyncio

    mock_tracer_obj, mock_span = mock_tracer

    with patch("overmind.tracing.get_tracer", return_value=mock_tracer_obj):

        @observe(span_name="async_operation")
        async def async_add(a: int, b: int):
            await asyncio.sleep(0.01)
            return a + b

        result = asyncio.run(async_add(10, 20))

        assert result == 30
        mock_tracer_obj.start_as_current_span.assert_called_once()
        assert mock_tracer_obj.start_as_current_span.call_args.args == ("async_operation",)
        mock_span.set_status.assert_called_once()
        assert mock_span.set_status.call_args[0][0].status_code == StatusCode.OK


def test_observe_async_with_exception(mock_tracer):
    import asyncio

    mock_tracer_obj, mock_span = mock_tracer

    with patch("overmind.tracing.get_tracer", return_value=mock_tracer_obj):

        @observe()
        async def async_fail():
            await asyncio.sleep(0.01)
            raise RuntimeError("Async error")

        with pytest.raises(RuntimeError, match="Async error"):
            asyncio.run(async_fail())

        mock_span.record_exception.assert_called_once()
        status_calls = list(mock_span.set_status.call_args_list)
        assert status_calls[-1][0][0].status_code == StatusCode.ERROR


def test_observe_async_cancellation_marks_cancelled(mock_tracer):
    """asyncio.CancelledError ends the span cancelled, not failed, and propagates."""
    import asyncio

    mock_tracer_obj, mock_span = mock_tracer

    with patch("overmind.tracing.get_tracer", return_value=mock_tracer_obj):

        @observe()
        async def cancelled_op():
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(cancelled_op())

        attributes = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
        assert attributes["overmind.status"] == "cancelled"
        assert "overmind.error.type" not in attributes
        mock_span.record_exception.assert_called_once()


def test_observe_preserves_function_metadata(mock_tracer):

    mock_tracer_obj, _mock_span = mock_tracer

    with patch("overmind.tracing.get_tracer", return_value=mock_tracer_obj):

        @observe()
        def documented_function(param: int) -> int:
            """This is a test function."""
            return param * 2

        assert documented_function.__name__ == "documented_function"
        assert documented_function.__doc__ == "This is a test function."


def test_observe_skips_cls_in_classmethod(mock_tracer):

    mock_tracer_obj, mock_span = mock_tracer

    with patch("overmind.tracing.get_tracer", return_value=mock_tracer_obj):

        class TestClass:
            class_value = 20

            @classmethod
            @observe()
            def class_method(cls, x: int, y: int):
                return cls.class_value + x + y

        result = TestClass.class_method(5, 3)

        assert result == 28
        # Check that inputs were captured but cls was skipped
        input_calls = [c for c in mock_span.set_attribute.call_args_list if "inputs" in str(c)]
        assert len(input_calls) > 0
        # Verify cls is not in the captured inputs
        captured_inputs = str(input_calls[0])
        assert "cls" not in captured_inputs or '"cls"' not in captured_inputs


def test_observe_async_class_method(mock_tracer):
    import asyncio

    mock_tracer_obj, _ = mock_tracer

    with patch("overmind.tracing.get_tracer", return_value=mock_tracer_obj):

        class AsyncTestClass:
            def __init__(self):
                self.value = 15

            @observe()
            async def async_method(self, x: int):
                await asyncio.sleep(0.01)
                return self.value * x

        obj = AsyncTestClass()
        result = asyncio.run(obj.async_method(2))

        assert result == 30
        mock_tracer_obj.start_as_current_span.assert_called_once()
