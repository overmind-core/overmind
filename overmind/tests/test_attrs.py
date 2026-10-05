"""Regression guards for ``overmind.attrs`` constant values.

These tests pin the wire-format namespaces so an accidental rename does not
silently break trace ingestion.

The CANONICAL token/cost namespace the Overmind server rolls up is ``genai.*``
(see ``overbae/api/overmind_attrs.py`` + ``otlp.py::_build_span_usage``).  The
SDK emits those keys AND, alongside them, the OTel GenAI semconv ``gen_ai.*``
keys (defined here as ``OTEL_*``) so OTel-native consumers and the optimiser's
:mod:`overmind.optimize.trace_reader` keep resolving model + tokens.
"""

from __future__ import annotations

from overmind import attrs


class TestLLMNamespace:
    """Canonical LLM_* constants must live in the server's ``genai.*`` namespace."""

    def test_all_llm_constants_start_with_genai(self) -> None:
        offenders = [
            (name, value)
            for name, value in vars(attrs).items()
            if name.startswith("LLM_") and isinstance(value, str) and not value.startswith("genai.")
        ]
        assert offenders == [], (
            f"Found LLM_* constants outside the genai.* namespace: {offenders}. "
            "The backend ingest rolls up genai.* keys; OTel semconv keys live under OTEL_*."
        )

    def test_otel_semconv_aliases_stay_in_gen_ai_namespace(self) -> None:
        # The dual-emitted OTel semconv keys the trace_reader depends on.
        assert attrs.OTEL_LLM_REQUEST_MODEL == "gen_ai.request.model"
        assert attrs.OTEL_LLM_SYSTEM == "gen_ai.system"
        assert attrs.OTEL_LLM_USAGE_PROMPT_TOKENS == "gen_ai.usage.prompt_tokens"
        assert attrs.OTEL_LLM_USAGE_COMPLETION_TOKENS == "gen_ai.usage.completion_tokens"
        assert attrs.OTEL_LLM_USAGE_TOTAL_TOKENS == "gen_ai.usage.total_tokens"


class TestBehaviourKey:
    def test_declared_task_key_is_pinned(self) -> None:
        assert attrs.BEHAVIOUR_KEY == "overmind.behaviour.key"


class TestErrorSummary:
    def test_error_summary_key(self) -> None:
        assert attrs.ERROR_SUMMARY == "overmind.error"
