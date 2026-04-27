"""Tests for overclaw.optimize.failure_classifier."""

from __future__ import annotations

from overclaw.optimize.failure_classifier import (
    FailureMode,
    classify_failure,
    is_recoverable_via_shadow,
)


def _classify(**kwargs):
    defaults = {"stderr": "", "returncode": 1, "timed_out": False, "error": ""}
    defaults.update(kwargs)
    return classify_failure(**defaults)


class TestClassifyFailureSuccess:
    def test_returncode_0_is_none(self):
        diag = classify_failure(stderr="", returncode=0)
        assert diag.mode == FailureMode.NONE
        assert diag.retryable is False


class TestClassifyFailureTimeout:
    def test_timeout_takes_precedence(self):
        diag = _classify(
            timed_out=True, error="Agent timed out after 120s", returncode=-1
        )
        assert diag.mode == FailureMode.TIMEOUT
        assert diag.retryable is True
        assert "shadow" in diag.remediation.lower()


class TestClassifyFailurePatterns:
    def test_api_key_missing(self):
        diag = _classify(stderr="Traceback: AuthenticationError: Invalid API key")
        assert diag.mode == FailureMode.API_KEY_MISSING
        # Retryable via shadow + cassette replay (cassette may have the
        # specific LLM call recorded so the missing key is bypassed).
        assert diag.retryable is True

    def test_api_key_env_var_name(self):
        diag = _classify(stderr="OPENAI_API_KEY environment variable not set")
        assert diag.mode == FailureMode.API_KEY_MISSING

    def test_browser_error_playwright(self):
        diag = _classify(
            stderr="playwright._impl._api_types.Error: Executable doesn't exist at"
        )
        assert diag.mode == FailureMode.BROWSER_RUNTIME_ERROR
        assert diag.retryable is True

    def test_browser_error_no_display(self):
        diag = _classify(stderr="Error: DISPLAY environment variable missing")
        assert diag.mode == FailureMode.BROWSER_RUNTIME_ERROR

    def test_missing_dependency(self):
        diag = _classify(stderr="ModuleNotFoundError: No module named 'foobar'")
        assert diag.mode == FailureMode.MISSING_DEPENDENCY
        assert diag.retryable is True

    def test_module_level_crash_argparse(self):
        diag = _classify(stderr="argparse: error: unrecognized arguments: --foo")
        assert diag.mode == FailureMode.MODULE_LEVEL_CRASH

    def test_module_level_crash_required_arg(self):
        diag = _classify(stderr="error: the following arguments are required: --url")
        assert diag.mode == FailureMode.MODULE_LEVEL_CRASH

    def test_schema_mismatch(self):
        diag = _classify(
            stderr=("TypeError: run() missing 1 required positional argument: 'url'")
        )
        assert diag.mode == FailureMode.SCHEMA_MISMATCH
        assert diag.retryable is True

    def test_network_error(self):
        diag = _classify(
            stderr="requests.exceptions.ConnectionError: Temporary failure in name resolution"
        )
        assert diag.mode == FailureMode.NETWORK_ERROR

    def test_syntax_error_is_import(self):
        diag = _classify(
            stderr='  File "foo.py", line 3\n    if x:\n    ^\nSyntaxError: invalid syntax'
        )
        assert diag.mode == FailureMode.IMPORT_ERROR
        assert diag.retryable is False

    def test_unknown_when_no_stderr(self):
        diag = _classify(stderr="", returncode=1, error="")
        assert diag.mode == FailureMode.UNKNOWN

    def test_crash_when_no_pattern_matches(self):
        diag = _classify(stderr="Something weird happened: KeyError bazbaz")
        assert diag.mode == FailureMode.CRASH


class TestExcerpt:
    def test_excerpt_truncates(self):
        long = "x" * 2000
        diag = _classify(stderr=f"AuthenticationError: bad key {long}")
        assert len(diag.excerpt) < 800


class TestRecoverability:
    def test_timeout_recoverable(self):
        diag = _classify(timed_out=True, returncode=-1)
        assert is_recoverable_via_shadow(diag) is True

    def test_import_error_not_recoverable(self):
        diag = _classify(stderr="SyntaxError: invalid syntax")
        assert is_recoverable_via_shadow(diag) is False

    def test_api_key_missing_recoverable_via_cassette(self):
        # Shadow + cassette replay may bypass the auth failure entirely if
        # the specific LLM payload was recorded previously.
        diag = _classify(stderr="AuthenticationError")
        assert is_recoverable_via_shadow(diag) is True

    def test_browser_recoverable(self):
        diag = _classify(stderr="playwright not installed")
        assert is_recoverable_via_shadow(diag) is True
