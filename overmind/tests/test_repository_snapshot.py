import json
import subprocess

import pytest
from overmind.config import Config, dump, load
from overmind.repository_snapshot import begin_repository_scan, capture_repository_snapshot, finish_repository_scan

from overmind.utils import convert_json_to_toml


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "scan@example.com")
    git(tmp_path, "config", "user.name", "Scan")
    (tmp_path / "agent.py").write_text("def run():\n    return 'hello'\n")
    git(tmp_path, "add", "agent.py")
    git(tmp_path, "commit", "-m", "Initial")
    return tmp_path


@pytest.mark.parametrize(
    "remote", ["https://user:secret@github.com/acme/agent.git?token=hidden", "git@github.com:acme/agent.git"]
)
def test_capture_strips_remote_credentials_and_identifies_clean_commit(repo, remote):
    git(repo, "remote", "add", "origin", remote)
    snapshot = capture_repository_snapshot(repo)
    assert snapshot["repository"] == "acme/agent"
    assert snapshot["commit"] == git(repo, "rev-parse", "HEAD")
    assert snapshot["branch"] == "main"
    assert snapshot["dirty"] is False
    assert "secret" not in json.dumps(snapshot)
    assert "hidden" not in json.dumps(snapshot)


def test_dirty_fingerprint_includes_untracked_files_and_excludes_generated_files(repo):
    clean = capture_repository_snapshot(repo)
    begin_repository_scan(repo)
    (repo / "overmind.toml").write_text('version = "0.2.1"')
    (repo / "overmind_capabilities.json").write_text("{}")
    assert capture_repository_snapshot(repo) == clean
    (repo / "new_tool.py").write_text("answer = 42")
    dirty = capture_repository_snapshot(repo)
    assert dirty["dirty"] is True
    assert dirty["fingerprint"] != clean["fingerprint"]
    (repo / "new_tool.py").write_text("answer = 43")
    assert capture_repository_snapshot(repo)["fingerprint"] != dirty["fingerprint"]


def test_scan_revision_survives_conversion_toml_and_sync_after_checkout_changes(repo):
    begin_repository_scan(repo)
    original = finish_repository_scan(repo)
    source = repo / "overmind_capabilities.json"
    source.write_text(json.dumps({"capabilities": []}))
    path = repo / "overmind.toml"
    convert_json_to_toml(source, path)
    git(repo, "switch", "-c", "later")
    (repo / "agent.py").write_text("changed after scanning")
    config = load(path)
    assert config.to_snapshot()["repository_snapshot"] == original
    pulled = Config()
    pulled.apply_snapshot(config.to_snapshot())
    dump(pulled, path)
    assert load(path).repository_snapshot == original


@pytest.mark.parametrize("change", ["content", "branch", "untracked"])
def test_scan_rejects_checkout_changes_before_conversion(repo, change):
    begin_repository_scan(repo)
    if change == "branch":
        git(repo, "switch", "-c", "other")
    else:
        (repo / ("agent.py" if change == "content" else "new.py")).write_text("changed")
    with pytest.raises(ValueError, match="Repository changed during the scan"):
        finish_repository_scan(repo)


def test_detached_head_and_missing_git_are_not_fabricated(repo, tmp_path):
    git(repo, "checkout", "--detach")
    assert capture_repository_snapshot(repo)["branch"] == ""
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    assert finish_repository_scan(unrelated) is None
    assert capture_repository_snapshot(tmp_path.parent) is None


def test_conversion_without_scan_receipt_clears_previous_provenance(repo):
    path = repo / "overmind.toml"
    dump(Config(repository_snapshot={"repository": "old"}), path)
    source = repo / "overmind_capabilities.json"
    source.write_text(json.dumps({"capabilities": []}))
    assert convert_json_to_toml(source, path).repository_snapshot is None
