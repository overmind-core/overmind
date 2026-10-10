import json
from pathlib import Path

import pytest

from overmind.skills_db import SKILLS_VERSION

SDK_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("harness", [".claude-plugin", ".codex-plugin", ".cursor-plugin"])
def test_plugin_manifest_ships_the_skills_version(harness):
    manifest = json.loads((SDK_ROOT / harness / "plugin.json").read_text())
    assert manifest["version"] == SKILLS_VERSION
