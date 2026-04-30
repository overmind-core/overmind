"""Tests for overmind.entrypoint_wrapper — wrapper injection helpers.

Focus on :func:`_prepend_sys_path_bootstrap`: it must preserve ``from
__future__`` imports at the top of the file, survive re-runs, and keep
shebangs / module docstrings in place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from overmind.entrypoint_wrapper import _prepend_sys_path_bootstrap


@pytest.fixture()
def agent_relpath(tmp_path: Path) -> Path:
    return Path("agents/demo/instrumented")


class TestPrependSysPathBootstrap:
    def test_future_import_stays_at_top(self, tmp_path: Path, agent_relpath: Path):
        wp = tmp_path / "wrapper.py"
        wp.write_text(
            "from __future__ import annotations\n"
            "import asyncio\n"
            "def run(x: dict) -> dict:\n"
            "    return {}\n",
            encoding="utf-8",
        )
        _prepend_sys_path_bootstrap(wp, agent_relpath)
        lines = wp.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "from __future__ import annotations"
        # Compile-check: the result must be syntactically valid Python
        compile(wp.read_text(), str(wp), "exec")

    def test_module_docstring_stays_on_top(self, tmp_path: Path, agent_relpath: Path):
        wp = tmp_path / "wrapper.py"
        wp.write_text(
            '"""Wrapper module docstring."""\n'
            "import asyncio\n"
            "def run(x: dict) -> dict:\n"
            "    return {}\n",
            encoding="utf-8",
        )
        _prepend_sys_path_bootstrap(wp, agent_relpath)
        out = wp.read_text(encoding="utf-8")
        assert out.splitlines()[0] == '"""Wrapper module docstring."""'
        compile(out, str(wp), "exec")

    def test_docstring_plus_future_import(self, tmp_path: Path, agent_relpath: Path):
        wp = tmp_path / "wrapper.py"
        wp.write_text(
            '"""Wrapper docstring."""\n'
            "from __future__ import annotations\n"
            "import asyncio\n"
            "def run(x: dict) -> dict:\n"
            "    return {}\n",
            encoding="utf-8",
        )
        _prepend_sys_path_bootstrap(wp, agent_relpath)
        lines = wp.read_text(encoding="utf-8").splitlines()
        assert lines[0] == '"""Wrapper docstring."""'
        assert lines[1] == "from __future__ import annotations"
        compile(wp.read_text(), str(wp), "exec")

    def test_multiline_docstring(self, tmp_path: Path, agent_relpath: Path):
        wp = tmp_path / "wrapper.py"
        wp.write_text(
            '"""Wrapper docstring.\n\nLines continue.\n"""\n'
            "from __future__ import annotations\n"
            "def run(x: dict) -> dict:\n"
            "    return {}\n",
            encoding="utf-8",
        )
        _prepend_sys_path_bootstrap(wp, agent_relpath)
        out = wp.read_text(encoding="utf-8")
        # The docstring block must appear before our bootstrap marker.
        assert out.find('"""Wrapper docstring.') < out.find(
            "OVERMIND_SYS_PATH_BOOTSTRAP"
        )
        compile(out, str(wp), "exec")

    def test_shebang_stays_first(self, tmp_path: Path, agent_relpath: Path):
        wp = tmp_path / "wrapper.py"
        wp.write_text(
            "#!/usr/bin/env python3\n"
            "from __future__ import annotations\n"
            "def run(x: dict) -> dict:\n"
            "    return {}\n",
            encoding="utf-8",
        )
        _prepend_sys_path_bootstrap(wp, agent_relpath)
        out = wp.read_text(encoding="utf-8")
        assert out.splitlines()[0] == "#!/usr/bin/env python3"
        compile(out, str(wp), "exec")

    def test_idempotent(self, tmp_path: Path, agent_relpath: Path):
        wp = tmp_path / "wrapper.py"
        wp.write_text(
            "from __future__ import annotations\n"
            "def run(x: dict) -> dict:\n"
            "    return {}\n",
            encoding="utf-8",
        )
        _prepend_sys_path_bootstrap(wp, agent_relpath)
        first = wp.read_text(encoding="utf-8")
        _prepend_sys_path_bootstrap(wp, agent_relpath)
        second = wp.read_text(encoding="utf-8")
        # Must not double-inject.
        assert first == second
        assert second.count("OVERMIND_SYS_PATH_BOOTSTRAP") == 2  # begin + end marker

    def test_no_future_import_path(self, tmp_path: Path, agent_relpath: Path):
        wp = tmp_path / "wrapper.py"
        wp.write_text(
            "import asyncio\ndef run(x: dict) -> dict:\n    return {}\n",
            encoding="utf-8",
        )
        _prepend_sys_path_bootstrap(wp, agent_relpath)
        out = wp.read_text(encoding="utf-8")
        # Bootstrap block lands ahead of the first real code statement.
        lines = out.splitlines()
        assert lines[0].startswith("# --- OVERMIND_SYS_PATH_BOOTSTRAP")
        compile(out, str(wp), "exec")
