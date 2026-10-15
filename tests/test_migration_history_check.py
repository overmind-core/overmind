import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "scripts/check_migrations.py"


@pytest.mark.parametrize("merged,modified", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("approved", [False, True])
def test_only_checksum_pinned_applied_histories_exempt_duplicate_numbers(
    tmp_path, merged, modified, approved
):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Migration test",
        "GIT_AUTHOR_EMAIL": "migration@example.test",
        "GIT_COMMITTER_NAME": "Migration test",
        "GIT_COMMITTER_EMAIL": "migration@example.test",
    }

    def git(*args):
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

    def commit(name):
        git("add", ".")
        git("commit", "-m", name)

    migrations = tmp_path / "overbae/migrations"
    migrations.mkdir(parents=True)
    git("init", "-b", "main")
    (migrations / "0001_initial.py").write_text("initial = True\n")
    commit("Initial")
    git("checkout", "-b", "native")
    native = migrations / "0002_native.py"
    native.write_text("native = True\n")
    commit("Native training")
    git("checkout", "main")
    (migrations / "0002_imports.py").write_text("imports = True\n")
    commit("Source imports")
    git("checkout", "native")
    if merged:
        git("merge", "--no-ff", "main", "-m", "Combine histories")
    else:
        (migrations / "0002_imports.py").write_text("imports = True\n")
        commit("Duplicate number without combined history")
    checker = tmp_path / "check_migrations.py"
    checker.write_text(CHECKER.read_text())
    (tmp_path / "applied-migration-history.json").write_text(
        json.dumps(
            {"0002_native.py": [hashlib.sha256(native.read_bytes()).hexdigest()]}
            if approved
            else {}
        )
    )
    if modified:
        native.write_text("native = False\n")
    result = subprocess.run(
        [sys.executable, str(checker), "main"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is (approved and not modified), result.stdout + result.stderr
