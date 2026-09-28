import json
from pathlib import Path

from overmind.config import load
from overmind.utils import convert_json_to_toml


def test_convert_enriches_then_stamps_verified(tmp_path: Path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "agent.py").write_text(
        'PROMPT = """You are triage."""\n'
        "def call_llm_tools(messages, tools):\n"
        "    return None\n"
        "def run_turn(user_text):\n"
        "    return call_llm_tools(user_text, [])\n"
    )
    src = tmp_path / "overmind_capabilities.json"
    src.write_text(
        json.dumps({
            "repo_summary": "demo",
            "capabilities": [
                {
                    "slug_hint": "triage",
                    "name": "Triage",
                    "system_prompt": "",
                    "system_prompt_span": "app/agent.py#L1-L1",
                    "capability_card": {
                        "task": "triage",
                        "anchors": [
                            {
                                "qualname": "app.agent.run_turn",
                                "kind": "entry_point",
                                "file": "app/agent.py#L4-L5",
                            },
                            {
                                "qualname": "app.agent.fabricated",
                                "kind": "tool",
                                "file": "app/agent.py#L99-L99",
                            },
                        ],
                        "trajectory_map": [
                            {
                                "id": "handle-turn",
                                "anchors": ["app.agent.run_turn", "app.agent.call_llm_tools"],
                            }
                        ],
                        "provenance": {"paths": ["app/agent.py#L4-L5", "nope.py#L1-L1"]},
                    },
                    "eval_matrix": [{"name": "Quality", "type": "llm_judge_custom", "rubric": "score"}],
                }
            ],
        })
    )
    dest = tmp_path / "overmind.toml"
    convert_json_to_toml(src, dest, repo_root=tmp_path)
    cap = load(dest).capabilities["triage"]
    assert cap.system_prompt == "You are triage."
    card = cap.capability_card
    assert [a["qualname"] for a in card["anchors"]] == ["app.agent.run_turn"]
    assert card["provenance"]["paths"] == ["app/agent.py#L4-L5"]
    paths = {p["id"]: p for p in card["trajectory_map"]}
    assert paths["handle-turn"]["anchors"] == ["app.agent.run_turn"]
    assert paths["handle-turn"]["verified"] is True
