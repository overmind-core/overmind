"""Provider spans include prompt and completion text unless the caller opts out."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from overmind import tracing

pytestmark = pytest.mark.covers("sdk-wire")

_CONTENT_ENV = (*tracing._INSTRUMENTOR_CONTENT_ENVS, *tracing._OPENINFERENCE_HIDE_ENVS)


@pytest.fixture(autouse=True)
def _reset_content_policy():
    saved_override = tracing._trace_content_override
    saved_providers = set(tracing._providers)
    saved_env = {key: os.environ.get(key) for key in (_CONTENT_ENV + (tracing._TRACE_CONTENT_ENV,))}
    tracing._trace_content_override = None
    tracing._providers.discard("langchain")
    for key in saved_env:
        os.environ.pop(key, None)
    yield
    tracing._trace_content_override = saved_override
    tracing._providers.clear()
    tracing._providers.update(saved_providers)
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _content_envs() -> dict[str, str]:
    return {key: os.environ[key] for key in tracing._INSTRUMENTOR_CONTENT_ENVS}


def test_provider_content_is_sent_by_default():
    tracing.enable_tracing(None)

    assert _content_envs() == dict.fromkeys(tracing._INSTRUMENTOR_CONTENT_ENVS, "true")
    assert os.environ["OPENINFERENCE_HIDE_INPUTS"] == "false"
    assert os.environ["OPENINFERENCE_HIDE_OUTPUTS"] == "false"


def test_overmind_env_omits_provider_content(monkeypatch):
    monkeypatch.setenv("OVERMIND_TRACE_CONTENT", "false")

    tracing.enable_tracing(None)

    assert _content_envs() == dict.fromkeys(tracing._INSTRUMENTOR_CONTENT_ENVS, "false")
    assert os.environ["OPENINFERENCE_HIDE_INPUT_MESSAGES"] == "true"
    assert os.environ["OPENINFERENCE_HIDE_OUTPUT_MESSAGES"] == "true"


def test_otel_env_omits_provider_content_when_overmind_flag_is_unset(monkeypatch):
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false")

    tracing.enable_tracing(None)

    assert os.environ["TRACELOOP_TRACE_CONTENT"] == "false"


def test_disabled_content_hides_langchain_payloads():
    tracing._trace_content_override = False
    instrumentor_cls = MagicMock()
    module = MagicMock(LangChainInstrumentor=instrumentor_cls)
    with (
        patch("importlib.util.find_spec", return_value=object()),
        patch("importlib.import_module", return_value=module),
    ):
        tracing._providers.discard("langchain")
        tracing.enable_tracing(["langchain"])

    config = instrumentor_cls.return_value.instrument.call_args.kwargs["config"]
    assert config.hide_inputs is True
    assert config.hide_outputs is True
    assert config.hide_input_messages is True
    assert config.hide_output_messages is True
