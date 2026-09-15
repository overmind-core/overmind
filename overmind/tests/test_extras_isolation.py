"""Default-install isolation: CLI and Client load without the tracing extra.

Runs in a subprocess so blocked OpenTelemetry modules cannot leak in from the
test session's already-imported state.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

_SCRIPT = textwrap.dedent("""
    import sys

    for blocked in (
        "opentelemetry",
        "opentelemetry.sdk",
        "opentelemetry.overmind",
    ):
        sys.modules[blocked] = None

    import overmind  # noqa: E402 — CLI default must import without OTel
    from overmind.client import Client  # noqa: E402

    assert overmind.Client is Client
    assert Client is not None

    import overmind.__main__  # noqa: E402

    try:
        overmind.init  # noqa: B018 — attribute access must not succeed
    except ImportError as exc:
        if "overmind[tracing]" not in str(exc):
            raise AssertionError(f"missing extra hint: {exc}") from exc
    else:
        raise AssertionError("overmind.init resolved without OpenTelemetry")

    print("EXTRAS-ISOLATION-OK")
""")


def test_cli_surface_works_without_tracing_extra():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "EXTRAS-ISOLATION-OK" in result.stdout
