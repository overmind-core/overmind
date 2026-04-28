"""Tests for ``overclaw.core.tracer`` — the thin shim over ``overmind``.

The legacy in-process ``Tracer`` / ``Span`` / ``Trace`` types and the
``set_current_tracer`` / ``get_current_tracer`` thread-locals were
removed; spans now flow through ``overmind`` (OpenTelemetry).
These tests cover the remaining public surface: ``call_llm`` and
``call_tool`` execute the underlying call and surface metadata via
``overmind.set_tag``.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from overclaw.core.tracer import call_llm, call_tool


@contextmanager
def _noop_span(*args, **kwargs):
    """Stand-in for ``overmind.start_span``; bypasses SDK init for tests."""
    yield MagicMock()


@pytest.fixture(autouse=True)
def _patch_span():
    """Replace the SDK ``start_span`` with a no-op so tests don't need ``overmind.init()``."""
    with patch("overclaw.core.tracer._span", side_effect=_noop_span):
        yield


def _mock_response(content: str = "hello", tokens: int = 10) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.usage.prompt_tokens = tokens
    mock_resp.usage.completion_tokens = tokens
    mock_resp.usage.total_tokens = tokens * 2
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = content
    mock_resp.choices[0].message.tool_calls = None
    return mock_resp


# ---------------------------------------------------------------------------
# call_llm
# ---------------------------------------------------------------------------


class TestCallLlm:
    @patch("overclaw.core.tracer.set_tag")
    @patch("overclaw.core.tracer.litellm")
    @patch("overclaw.utils.llm.litellm")
    def test_success(self, mock_lp, mock_tracer_lib, mock_set_tag):
        mock_lp.completion.return_value = _mock_response()
        mock_tracer_lib.completion_cost.return_value = 0.001

        result = call_llm("test-model", [{"role": "user", "content": "hi"}])

        assert result.choices[0].message.content == "hello"
        tag_keys = {call.args[0] for call in mock_set_tag.call_args_list}
        assert {
            "llm.model",
            "llm.messages_count",
            "llm.prompt_tokens",
            "llm.completion_tokens",
            "llm.total_tokens",
            "llm.cost",
        }.issubset(tag_keys)

    @patch("overclaw.core.tracer.set_tag")
    @patch("overclaw.core.tracer.litellm")
    @patch("overclaw.utils.llm.litellm")
    def test_error_propagated_and_tagged(
        self, mock_lp, mock_tracer_lib, mock_set_tag
    ):
        mock_lp.completion.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            call_llm("model", [{"role": "user", "content": "hi"}])

        tag_keys = {call.args[0] for call in mock_set_tag.call_args_list}
        assert "llm.error" in tag_keys

    @patch("overclaw.core.tracer.set_tag")
    @patch("overclaw.core.tracer.litellm")
    @patch("overclaw.utils.llm.litellm")
    def test_with_tools(self, mock_lp, mock_tracer_lib, mock_set_tag):
        mock_resp = _mock_response()
        tc = MagicMock()
        tc.function.name = "search"
        tc.function.arguments = '{"q": "test"}'
        mock_resp.choices[0].message.tool_calls = [tc]
        mock_lp.completion.return_value = mock_resp
        mock_tracer_lib.completion_cost.return_value = 0.0

        result = call_llm(
            "model",
            [{"role": "user", "content": "hi"}],
            tools=[{"function": {"name": "search"}}],
        )

        assert result is not None
        tag_keys = {call.args[0] for call in mock_set_tag.call_args_list}
        assert "llm.tools_provided" in tag_keys
        assert "llm.tool_calls" in tag_keys

    @patch("overclaw.core.tracer.set_tag")
    @patch("overclaw.core.tracer.litellm")
    @patch("overclaw.utils.llm.litellm")
    def test_cost_exception_swallowed(
        self, mock_lp, mock_tracer_lib, mock_set_tag
    ):
        mock_lp.completion.return_value = _mock_response()
        mock_tracer_lib.completion_cost.side_effect = Exception("cost error")

        result = call_llm("model", [{"role": "user", "content": "hi"}])
        assert result is not None


# ---------------------------------------------------------------------------
# call_tool
# ---------------------------------------------------------------------------


class TestCallTool:
    @patch("overclaw.core.tracer.set_tag")
    def test_success_invokes_fn_with_kwargs(self, mock_set_tag):
        fn = MagicMock(return_value={"result": 42})
        result = call_tool("my_tool", {"x": 1}, fn)
        assert result == {"result": 42}
        fn.assert_called_once_with(x=1)
        tag_keys = {call.args[0] for call in mock_set_tag.call_args_list}
        assert {"tool.name", "tool.arg_keys"}.issubset(tag_keys)

    @patch("overclaw.core.tracer.set_tag")
    def test_error_propagated_and_tagged(self, mock_set_tag):
        fn = MagicMock(side_effect=ValueError("bad"))
        with pytest.raises(ValueError, match="bad"):
            call_tool("tool", {"k": "v"}, fn)
        tag_keys = {call.args[0] for call in mock_set_tag.call_args_list}
        assert "tool.error" in tag_keys
