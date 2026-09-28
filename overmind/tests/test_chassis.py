import json
import textwrap
from pathlib import Path

from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.chassis import (
    chassis_digest,
    extract_chassis,
    reachable_names,
    verify_trajectory_claims,
)
from overmind.config import load
from overmind.utils import convert_json_to_toml

AGENT_SRC = textwrap.dedent(
    """
    def call_llm_tools(messages, tools):
        return client.chat.completions.create(messages=messages, tools=tools)

    def run_turn(user_text):
        context = assemble_context(user_text)
        reply = call_llm_tools(context, [])
        return emit_answer(reply)

    def assemble_context(user_text):
        return [user_text]

    def emit_answer(reply):
        return reply

    def orphan_helper():
        return None
    """
)


def _repo(tmp_path: Path) -> str:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "agent.py").write_text(AGENT_SRC, encoding="utf-8")
    return str(tmp_path)


def test_digest_lists_verbatim_qualnames_per_file(tmp_path):
    chassis = extract_chassis(_repo(tmp_path))
    digest = chassis_digest(chassis)
    assert digest.splitlines()[0].startswith("5 functions across 1 files")
    assert "app/agent.py: " in digest
    assert "app.agent.run_turn" in digest


def test_digest_caps_output(tmp_path):
    chassis = extract_chassis(_repo(tmp_path))
    digest = chassis_digest(chassis, max_files=1, max_per_file=2)
    assert "(+3 more)" in digest


def test_reachability_from_entry(tmp_path):
    chassis = extract_chassis(_repo(tmp_path))
    reached = reachable_names("app.agent.run_turn", chassis)
    assert {"assemble_context", "call_llm_tools", "emit_answer"} <= reached
    assert "orphan_helper" not in reached


def test_verify_trajectory_claims_stamps_verified(tmp_path):
    chassis = extract_chassis(_repo(tmp_path))
    card = {
        "trajectory_map": [
            {
                "id": "handle-turn",
                "claim": "decision_surface",
                "anchors": ["app.agent.run_turn", "app.agent.call_llm_tools"],
            },
            {
                "id": "fabricated",
                "claim": "code_path",
                "anchors": ["app.agent.run_turn", "app.agent.does_not_exist"],
            },
            {"id": "anchorless", "claim": "code_path", "anchors": []},
        ]
    }
    verify_trajectory_claims(card, chassis)
    by_id = {e["id"]: e for e in card["trajectory_map"]}
    assert by_id["handle-turn"]["verified"] is True
    assert by_id["fabricated"]["verified"] is False
    assert by_id["anchorless"]["verified"] is False


def test_convert_json_to_toml_stamps_verified(tmp_path):
    _repo(tmp_path)
    src = tmp_path / "overmind_capabilities.json"
    src.write_text(
        json.dumps({
            "repo_summary": "demo",
            "capabilities": [
                {
                    "slug_hint": "triage",
                    "name": "Triage",
                    "capability_card": {
                        "task": "triage",
                        "anchors": [
                            {
                                "qualname": "app.agent.run_turn",
                                "kind": "entry_point",
                                "file": "app/agent.py",
                            },
                            {
                                "qualname": "app.agent.call_llm_tools",
                                "kind": "function",
                                "file": "app/agent.py",
                            },
                        ],
                        "trajectory_map": [
                            {
                                "id": "handle-turn",
                                "anchors": ["app.agent.run_turn", "app.agent.call_llm_tools"],
                            },
                            {
                                "id": "fabricated",
                                "anchors": ["app.agent.does_not_exist"],
                            },
                        ],
                    },
                    "eval_matrix": [{"name": "Quality", "type": "custom_judge", "rubric": "score"}],
                }
            ],
        })
    )
    dest = tmp_path / "overmind.toml"
    convert_json_to_toml(src, dest, repo_root=tmp_path)
    paths = {p["id"]: p for p in load(dest).capabilities["triage"].capability_card["trajectory_map"]}
    assert paths["handle-turn"]["verified"] is True
    assert paths["fabricated"]["verified"] is False


def test_chassis_cli_prints_digest(tmp_path, monkeypatch):
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["chassis"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "app.agent.run_turn" in result.output
    assert "5 functions across 1 files" in result.output
