"""Manifest AST scan — decorator call sites become DeclaredSymbols."""

from __future__ import annotations

from pathlib import Path

from overmind.manifest import scan


def test_scan_collects_capability_tool_llm_and_expectations(tmp_path: Path):
    (tmp_path / "agent.py").write_text(
        '''
from overmind import capability, observe, task, Expectation

TRIAGE_PROMPT = "You are a triage agent."

@capability("triage", description="Resolve a support ticket end to end")
def handle_ticket(ticket: str) -> str:
    with task("refund-flow", unit="turn"):
        order = lookup_order(ticket)
        return decide(ticket, order)

@observe(type="tool")
def lookup_order(order_id: str) -> dict:
    """Look up an order by id."""
    return {"id": order_id}

@observe(type="llm", prompt=TRIAGE_PROMPT,
         expectations=[Expectation("constraint", "cites the refund policy")])
def decide(ticket: str, order: dict) -> str:
    return "ok"
''',
        encoding="utf-8",
    )
    manifest = scan(tmp_path)
    by_role = {s.role: s for s in manifest.symbols if s.role != "task"}
    assert set(by_role) >= {"capability", "tool", "llm"}
    cap = by_role["capability"]
    assert cap.slug == "triage"
    assert "lookup_order" in " ".join(cap.calls) or any("lookup_order" in c for c in cap.calls)
    tool = by_role["tool"]
    assert tool.signature["params"][0]["name"] == "order_id"
    llm = by_role["llm"]
    assert llm.prompt_template == "You are a triage agent."
    assert llm.expectations == [{"kind": "constraint", "spec": "cites the refund policy"}]
    assert llm.unresolved == []
    tasks = [s for s in manifest.symbols if s.role == "task"]
    assert any(t.task_key == "refund-flow" for t in tasks)


def test_scan_kimm_research_agent():
    root = Path(__file__).resolve().parents[2]
    kimm = root / "kimm.py"
    assert kimm.is_file()
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    try:
        shutil.copy(kimm, tmp / "kimm.py")
        manifest = scan(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    roles = {s.role for s in manifest.symbols}
    assert {"capability", "tool", "task", "llm"} <= roles
    research = next(s for s in manifest.symbols if s.role == "capability")
    assert research.slug == "research"
    assert any(s.task_key == "research-run" for s in manifest.symbols if s.role == "task")
    assert any(s.task_key == "dig-query" for s in manifest.symbols if s.role == "task")
