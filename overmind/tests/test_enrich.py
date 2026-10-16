import json
from pathlib import Path

import pytest

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
                        "tool_spec": [
                            {
                                "name": "search",
                                "branches": [
                                    {
                                        "condition": "When requested",
                                        "basis": "code",
                                        "provenance": ["app/agent.py#L2-L3", "missing.py#L1-L2"],
                                    }
                                ],
                            }
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

    assert card["tool_spec"][0]["branches"][0]["provenance"] == ["app/agent.py#L2-L3"]


@pytest.fixture
def decision_scan(tmp_path):
    source = tmp_path / "agent.py"
    source.write_text(
        "def run():\n    observe(); decide(); dispatch(); record()\n"
        "def observe():\n    return 'state'\n"
        "def decide():\n    return 'action'\n"
        "def dispatch():\n    return 'result'\n"
        "def record():\n    return compact()\n"
        "def compact():\n    return 'history'\n"
    )
    (tmp_path / "model.py").write_text(
        "def resolve(explicit=None, configured=None):\n"
        "    return explicit or configured or 'small-1'\n"
        "def template(model):\n"
        "    return 'special.md' if 'provider/' in model else 'default.md'\n"
    )
    (tmp_path / "default.md").write_text('Choose at most {limit} actions. Return {{"action": "..."}}.\n')
    (tmp_path / "special.md").write_text("Use the provider-specific instructions.\n")
    anchors = [
        {"qualname": f"agent.{name}", "kind": "function", "file": f"agent.py#L{line}-L{line + 1}"}
        for name, line in [("run", 1), ("observe", 3), ("decide", 5), ("dispatch", 7), ("record", 9), ("compact", 11)]
    ]
    steps = [
        {
            "step": name,
            "kind": "model_invocation" if name == "decide" else "agent_step",
            "anchors": [f"agent.{name}"],
            "input": "state and prior results",
            "action": "choose a registered action",
            "output": "action or completion",
            "transitions": [{"target": f"agent.{target}", "condition": "continue"}],
        }
        for name, target in [
            ("observe", "decide"),
            ("decide", "dispatch"),
            ("dispatch", "record"),
            ("record", "observe"),
        ]
    ]
    return {
        "llm_utilities": [
            {
                "name": "Compaction",
                "called_by": "browser",
                "source_path": "agent.py",
                "entrypoint_fn": "compact",
                "anchors": ["agent.compact"],
                "provenance": {"paths": ["agent.py#L11-L12"]},
            }
        ],
        "capabilities": [
            {
                "slug_hint": "browser",
                "name": "Browser",
                "capability_card": {
                    "model_resolution": {
                        "selection": "Explicit model, then configured model, then small-1; active runtime value is unknown.",
                        "default_model": "small-1",
                        "provenance": ["model.py#L1-L2"],
                    },
                    "prompt_resolution": {
                        "selection": "small-1 lacks provider/, so the default uses default.md, not special.md.",
                        "default_span": "default.md#L1-L1",
                        "bindings": {"limit": 5},
                        "provenance": ["model.py#L3-L4"],
                    },
                    "anchors": anchors,
                    "tool_spec": [
                        {
                            "name": name,
                            "branches": [
                                {
                                    "condition": "action is selected",
                                    "basis": "code",
                                    "action": "execute action",
                                    "outcome": "action result",
                                    "provenance": ["agent.py#L7-L8"],
                                    "transitions": [{"target": "agent.record", "condition": "action returns"}],
                                }
                            ],
                        }
                        for name in ["navigate", "done"]
                    ],
                    "trajectory_map": [
                        {
                            "id": "browser-loop",
                            "claim": "decision_surface",
                            "anchors": [anchor["qualname"] for anchor in anchors],
                            "sequence": steps,
                        }
                    ],
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "defect",
    [
        "empty_branches",
        "missing_outcome",
        "invalid_provenance",
        "unknown_target",
        "missing_model_io",
        "missing_step_anchor",
        "summary_only",
        "missing_model_resolution",
        "missing_prompt_resolution",
        "invalid_resolution_provenance",
        "wrong_prompt_text",
        "missing_prompt_source",
        "invalid_prompt_bindings",
        "missing_utility_anchor",
        "fabricated_utility_anchor",
        "utility_wrapper_only",
    ],
)
def test_incomplete_scan_preserves_previous_snapshot(tmp_path, decision_scan, defect):
    card = decision_scan["capabilities"][0]["capability_card"]
    steps = card["trajectory_map"][0]["sequence"]
    branch = card["tool_spec"][0]["branches"][0]
    if defect == "empty_branches":
        card["tool_spec"][0]["branches"] = []
    elif defect == "missing_outcome":
        branch["outcome"] = ""
    elif defect == "invalid_provenance":
        branch["provenance"] = ["missing.py#L1-L2"]
    elif defect == "unknown_target":
        branch["transitions"][0]["target"] = "model-decision"
    elif defect == "missing_model_io":
        steps[1]["output"] = ""
    elif defect == "missing_step_anchor":
        steps[2]["anchors"] = ["agent.invented_dispatch"]
    elif defect == "summary_only":
        card["trajectory_map"][0]["sequence"] = ["observe", "decide", "act"]
    elif defect == "missing_model_resolution":
        del card["model_resolution"]
    elif defect == "missing_prompt_resolution":
        del card["prompt_resolution"]
    elif defect == "invalid_resolution_provenance":
        card["model_resolution"]["provenance"] = ["missing.py#L1-L2"]
    elif defect == "wrong_prompt_text":
        decision_scan["capabilities"][0]["system_prompt"] = (tmp_path / "special.md").read_text()
    elif defect == "missing_prompt_source":
        card["prompt_resolution"]["default_span"] = "missing.md#L1-L1"
    elif defect == "invalid_prompt_bindings":
        card["prompt_resolution"]["bindings"] = {}
    elif defect == "missing_utility_anchor":
        card["anchors"] = [anchor for anchor in card["anchors"] if anchor["qualname"] != "agent.compact"]
    elif defect == "fabricated_utility_anchor":
        decision_scan["llm_utilities"][0]["anchors"] = ["agent.invented"]
    elif defect == "utility_wrapper_only":
        decision_scan["llm_utilities"][0]["anchors"] = ["agent.record"]
    src = tmp_path / "overmind_capabilities.json"
    src.write_text(json.dumps(decision_scan))
    dest = tmp_path / "overmind.toml"
    dest.write_text('project-name = "preserved"\n')
    with pytest.raises(ValueError, match="Incomplete capability scan"):
        convert_json_to_toml(src, dest)
    assert dest.read_text() == 'project-name = "preserved"\n'


def test_detailed_scan_preserves_branches_and_loop_transitions(tmp_path, decision_scan):
    decision_scan["capabilities"][0]["capability_card"]["trajectory_map"].append({
        "id": "declared-task",
        "claim": "declared_task",
        "prompt_quote": "Find the requested page",
        "sequence": [{"step": "Find the page", "anchors": []}],
    })
    src = tmp_path / "overmind_capabilities.json"
    src.write_text(json.dumps(decision_scan))
    dest = tmp_path / "overmind.toml"
    dest.write_text('[capabilities.browser]\nname = "Browser"\nid = "existing-capability"\n')
    convert_json_to_toml(src, dest)
    capability = load(dest).capabilities["browser"]
    assert capability.id == "existing-capability"
    assert capability.system_prompt == 'Choose at most 5 actions. Return {"action": "..."}.'
    card = capability.capability_card
    assert card["model_resolution"]["default_model"] == "small-1"
    assert card["llm_utilities"][0]["anchors"] == ["agent.compact"]
    assert [len(tool["branches"]) for tool in card["tool_spec"]] == [1, 1]
    path = card["trajectory_map"][0]
    assert path["verified"] is True
    assert len(path["sequence"]) == 4
    assert path["sequence"][-1]["transitions"][0]["target"] == "agent.observe"


def test_dynamic_resolution_preserves_explicit_unknowns(tmp_path, decision_scan):
    card = decision_scan["capabilities"][0]["capability_card"]
    for key, default_key in (("model_resolution", "default_model"), ("prompt_resolution", "default_span")):
        card[key][default_key] = ""
        card[key]["unresolved"] = "Host injects the value at runtime; the checkout contains no default."
    utility = decision_scan["llm_utilities"][0]
    utility["anchors"] = []
    utility["unresolved"] = "Implementation is provided by the host; source is unavailable."
    src = tmp_path / "overmind_capabilities.json"
    src.write_text(json.dumps(decision_scan))
    dest = tmp_path / "overmind.toml"
    convert_json_to_toml(src, dest)
    cap = load(dest).capabilities["browser"]
    assert cap.model == cap.system_prompt == ""
    assert cap.capability_card["prompt_resolution"]["unresolved"]
    assert cap.capability_card["llm_utilities"][0]["unresolved"]
