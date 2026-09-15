import textwrap

from overbae.services.codebase.chassis import (
    chassis_digest,
    extract_chassis,
    reachable_names,
    verify_trajectory_claims,
)

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


def _repo(tmp_path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "agent.py").write_text(AGENT_SRC, encoding="utf-8")
    return str(tmp_path)


def test_extracts_functions_and_call_graph(tmp_path):
    chassis = extract_chassis(_repo(tmp_path))
    assert "app.agent.run_turn" in chassis.functions
    assert "call_llm_tools" in chassis.calls["app.agent.run_turn"]


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
