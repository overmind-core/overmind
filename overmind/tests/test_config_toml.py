import json
import subprocess
from pathlib import Path

import pytest

from overmind.config import (
    Capability,
    Config,
    SecretFileError,
    credentials_path,
    dump,
    load,
    protect_secret_file,
)
from overmind.utils import convert_json_to_toml


def test_dump_writes_nested_capability_card(tmp_path: Path):
    path = tmp_path / "overmind.toml"
    dump(
        Config(
            api_key="k",
            project_id="project-1",
            capabilities={
                "demo": Capability(
                    slug="demo",
                    name="Demo",
                    capability_card={
                        "task": "answer",
                        "vocabulary": {"context": "scraped"},
                        "trajectory_map": [
                            {
                                "id": "main",
                                "sequence": [
                                    {
                                        "kind": "model_invocation",
                                        "may_use": [{"tool": "search", "when": "always"}],
                                    }
                                ],
                            }
                        ],
                    },
                    eval_matrix=[{"name": "Faithfulness", "type": "managed", "rubric": ""}],
                )
            },
        ),
        path,
    )
    text = path.read_text()
    assert "api-key" not in text
    assert "[capabilities.demo.capability_card]" in text
    assert 'task = "answer"' in text
    assert "[[capabilities.demo.capability_card.trajectory_map]]" in text
    assert 'capability_card = "' not in text
    assert 'eval_matrix = "' not in text

    loaded = load(path)
    card = loaded.capabilities["demo"].capability_card
    assert card["task"] == "answer"
    assert card["vocabulary"]["context"] == "scraped"
    assert card["trajectory_map"][0]["sequence"][0]["may_use"][0]["tool"] == "search"
    assert loaded.capabilities["demo"].eval_matrix[0]["name"] == "Faithfulness"
    assert loaded.api_key == "k"
    assert credentials_path(path).stat().st_mode & 0o777 == 0o600


def test_load_accepts_legacy_json_string_card(tmp_path: Path):
    path = tmp_path / "overmind.toml"
    card = {"task": "legacy", "trajectory_map": [{"id": "a"}]}
    path.write_text(
        'api-key = "k"\n'
        'base-url = "http://localhost:8000"\n'
        'project-id = ""\n'
        'repo_summary = ""\n'
        'trace-provider = "overmind"\n'
        'version = "0.2.1"\n\n'
        "[capabilities.demo]\n"
        'slug = "demo"\n'
        'name = "Demo"\n'
        f"capability_card = {json.dumps(json.dumps(card))}\n"
    )
    loaded = load(path)
    assert loaded.capabilities["demo"].capability_card["task"] == "legacy"
    assert loaded.api_key == "k"


def test_convert_json_to_toml_preserves_card_shape(tmp_path: Path):
    src = tmp_path / "overmind_capabilities.json"
    src.write_text(
        json.dumps({
            "repo_summary": "demo repo",
            "capabilities": [
                {
                    "slug_hint": "writer",
                    "name": "Writer",
                    "capability_card": {
                        "task": "write",
                        "trajectory_map": [{"id": "t1", "tools": ["search"]}],
                    },
                    "eval_matrix": [
                        {
                            "name": "Quality",
                            "type": "llm_judge_custom",
                            "rubric": "score 0-1",
                        }
                    ],
                }
            ],
        })
    )
    dest = tmp_path / "overmind.toml"
    convert_json_to_toml(src, dest)
    text = dest.read_text()
    assert "[capabilities.writer.capability_card]" in text
    assert "[[capabilities.writer.capability_card.trajectory_map]]" in text
    loaded = load(dest)
    assert loaded.capabilities["writer"].capability_card["trajectory_map"][0]["id"] == "t1"
    assert loaded.project_name == tmp_path.name


def test_credentials_only_apply_to_their_project(tmp_path: Path):
    path = tmp_path / "overmind.toml"
    dump(Config(api_key="project-a-key", project_id="project-a"), path)
    dump(Config(project_id="project-b"), path)

    assert load(path).api_key == ""


def test_secret_file_is_locally_excluded_and_must_not_be_tracked(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    secret = tmp_path / ".overmind" / "credentials.toml"

    protect_secret_file(secret, repo_root=tmp_path)

    exclude = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    )
    exclude_path = tmp_path / exclude.stdout.strip()
    assert "/.overmind/credentials.toml" in exclude_path.read_text().splitlines()

    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text('api-key = "tracked"\n')
    subprocess.run(["git", "add", "-f", ".overmind/credentials.toml"], cwd=tmp_path, check=True)
    with pytest.raises(SecretFileError, match="tracked file"):
        protect_secret_file(secret, repo_root=tmp_path)


def test_secret_file_rejects_symlinked_parent(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".overmind").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SecretFileError, match="through symlink"):
        protect_secret_file(tmp_path / ".overmind" / "credentials.toml", repo_root=tmp_path)


def test_secret_file_still_works_without_git(tmp_path: Path):
    secret = tmp_path / ".overmind" / "credentials.toml"

    def missing_git(*_args, **_kwargs):
        raise FileNotFoundError

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("overmind.config.subprocess.run", missing_git)
        protect_secret_file(secret, repo_root=tmp_path)
