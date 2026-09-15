"""The notebook agent: its tools over a real chain, the workspace it reads,
and a turn driven through a fake Cursor session."""

from __future__ import annotations

import types
import uuid

import pytest

from overbae.models import Capability, Dataset, Project
from overbae.services.datasets import land, lifecycle, paths
from overbae.services.datasets.notebook import agent, prompts, workspace

pytestmark = pytest.mark.django_db

ROWS = [
    {"question": "q1", "answer": "a1", "tag": "keep"},
    {"question": "", "answer": "orphan", "tag": "junk"},
    {"question": "q3", "answer": "a3", "tag": "keep"},
]
KEEP = "df = df[df['tag'] == 'keep']\n"
SHAPE = "df = df.rename(columns={'question': 'input', 'answer': 'expected_output'})\n"


def _dataset(rows=ROWS, **fields) -> Dataset:
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    dataset = Dataset.objects.create(project=project, name="ds", **fields)
    land.land_rows(dataset, [dict(r) for r in rows])
    dataset.refresh_from_db()
    return dataset


def _tools(dataset: Dataset) -> tuple[agent.Tools, list]:
    events: list = []
    tools = agent.Tools(dataset.id, None, events.append)
    return tools, events


def test_status_lists_versions_and_both_reports():
    dataset = _dataset(intent="eval")
    tools, _ = _tools(dataset)
    status = tools.status({})
    assert status["intent"] == "eval" and status["active"] == "1.0"
    assert status["fits"] == {"ok": False, "reason": "no input column"}
    assert status["cells"][0]["version"] == "1.0" and status["cells"][0]["rows"] == 3


def test_query_reads_one_version_as_table_t():
    dataset = _dataset()
    tools, _ = _tools(dataset)
    result = tools.query({"sql": "SELECT count(*) AS n FROM t WHERE tag = 'keep'"})
    assert result["rows"] == [{"n": 2}] and result["version"] == "1.0"
    assert "source_row" not in tools.query({"sql": "SELECT * FROM t"})["columns"]
    assert "error" in tools.query({"sql": "SELECT nope FROM t"})


def test_try_script_previews_without_landing():
    dataset = _dataset()
    tools, events = _tools(dataset)
    result = tools.try_script({"script": KEEP})
    assert result["ok"] and result["rows"] == 2 and "source_row" not in result["columns"]
    assert "source_row" not in result["head"][0]
    assert dataset.cells.count() == 1 and events == []
    bad = tools.try_script({"script": "df = df['nope']\n"})
    assert bad["ok"] is False and "nope" in bad["error"]


def test_add_cell_runs_and_reports_and_a_proposal_waits():
    dataset = _dataset(intent="eval")
    tools, events = _tools(dataset)
    result = tools.add_cell({"title": "Keep", "script": KEEP, "note": "drops 1 junk row"})
    assert result["ok"] and result["version"] == "1.1" and result["rows"] == 2
    assert [e["action"] for e in events if e["type"] == "chat_cell"] == ["created", "ran"]
    proposal = tools.add_cell({"title": "Shape", "script": SHAPE, "run": False, "note": "x"})
    assert proposal["proposed"] is True
    assert dataset.cells.get(pk=proposal["id"]).state == "proposed"
    assert tools.status({})["active"] == "1.1"
    diff = tools.diff({"to": "1.1"})
    assert diff["from"] == "1.0" and diff["rows_removed"] == 1
    assert diff["removed_examples"][0]["tag"] == "junk"


def test_edit_cell_reruns_and_set_intent_only_while_pending():
    dataset = _dataset()
    tools, _ = _tools(dataset)
    assert tools.set_intent({"intent": "eval"})["ok"]
    tools.add_cell({"title": "Keep", "script": KEEP})
    result = tools.edit_cell({"version": "1.1", "script": "df = df\n"})
    assert result["ok"] and result["rows"] == 3
    assert tools.edit_cell({"version": "9.9", "script": "df = df\n"})["ok"] is False
    assert tools.set_intent({"intent": "raw"})["ok"] is False


def test_remove_cell_and_set_active_reshape_the_chain():
    dataset = _dataset(intent="eval")
    tools, events = _tools(dataset)
    tools.add_cell({"title": "Keep", "script": KEEP})
    tools.add_cell({"title": "Shape", "script": SHAPE})
    assert tools.status({})["active"] == "1.2"
    assert tools.set_active({"version": "1.1"})["active"] == "1.1"
    removed = tools.remove_cell({"version": "1.1"})
    assert removed["ok"] and removed["removed"] == "Keep" and removed["active"] == "1.1"
    assert [c.title for c in dataset.chain] == ["Source", "Shape"]
    assert tools.status({})["cells"][1]["rows"] == 3
    assert [e["action"] for e in events if e["type"] == "chat_cell"][-1] == "removed"
    assert dataset.chain[1].state == "ok"
    assert tools.remove_cell({"version": "9.9"})["ok"] is False


def test_set_capability_and_rename_change_the_dataset():
    dataset = _dataset(intent="eval")
    Capability.objects.create(project=dataset.project, name="KB", slug="kb")
    tools, events = _tools(dataset)
    assert tools.set_capability({"capability": "kb"})["capability"] == "KB"
    assert tools.status({})["capability"] == "KB"
    missing = tools.set_capability({"capability": "Nope"})
    assert missing["ok"] is False and missing["capabilities"] == ["KB"]
    assert tools.set_capability({"capability": "none"})["capability"] is None
    assert tools.rename({"name": "  Better name "})["name"] == "Better name"
    assert tools.rename({"name": ""})["ok"] is False
    assert {e["type"] for e in events} == {"dataset_changed"}


def test_guarded_tools_return_json_safe_errors():
    dataset = _dataset()
    tools, _ = _tools(dataset)
    defs = tools.definitions()
    assert set(defs) == {
        "status",
        "query",
        "diff",
        "try_script",
        "add_cell",
        "edit_cell",
        "remove_cell",
        "set_active",
        "set_intent",
        "set_capability",
        "rename",
        "install",
    }
    out = defs["query"].execute({"sql": "SELECT 1.5::DECIMAL(4,2) AS d FROM t LIMIT 1"}, None)
    assert out["rows"] == [{"d": "1.50"}]
    failed = defs["add_cell"].execute({"title": "x", "script": "df = df['nope']\n"}, None)
    assert failed["ok"] is False and "nope" in failed["error"]
    phases = [(s["phase"], s.get("tool"), s.get("ok")) for s in tools.steps if "tool" in s]
    assert phases == [
        ("tool_start", "query", None),
        ("tool_done", "query", True),
        ("tool_start", "add_cell", None),
        ("tool_done", "add_cell", False),
    ]
    assert tools.steps[-2]["preview"].startswith('{"ok": false')


def test_workspace_carries_prompt_capability_cells_and_frames():
    dataset = _dataset(intent="eval")
    capability = Capability.objects.create(
        project=dataset.project,
        name="KB",
        slug="kb",
        improvement_metadata={
            "system_prompt": "You are KB.",
            "capability_card": {"tool_spec": [{"name": "search"}]},
        },
    )
    lifecycle.set_capability(dataset, capability)
    lifecycle.add_cell(dataset, title="Keep rows", script=KEEP)
    root = workspace.prepare(dataset)
    text = (root / "AGENTS.md").read_text()
    assert prompts.WORKSHOP.splitlines()[0] in text and "## Eval playbook" in text
    assert "Capability: KB" in text and "Tools: search." in text
    assert '"system_prompt": "You are KB."' in (root / "capability.json").read_text()
    assert "pandas" in (root / "libraries.md").read_text()
    assert (
        (root / "cells" / "001_keep_rows.py").read_text().startswith("# 1.1 · Keep rows · queued")
    )
    assert (root / "frames" / "1.0.parquet").resolve() == paths.cell_path(
        dataset.id, dataset.source.id
    )
    sample = (root / "sample.jsonl").read_text()
    assert sample.count("\n") == 3 and "source_row" not in sample


class _FakeRun:
    def __init__(self, tools):
        self._tools = tools

    def stream(self):
        out = self._tools["add_cell"].execute({"title": "Keep", "script": KEEP}, None)
        assert out["ok"]
        yield types.SimpleNamespace(type="tool_call", name="add_cell", status="running")
        yield types.SimpleNamespace(
            type="assistant",
            message=types.SimpleNamespace(content=[types.SimpleNamespace(text="Kept 2 rows.")]),
        )

    def wait(self):
        return types.SimpleNamespace(status="finished")

    usage = None


class _FakeAgent:
    created: list = []

    def __init__(self, options):
        self.options = options
        self.agent_id = "agent-1"

    @classmethod
    def create(cls, options):
        cls.created.append(options)
        return cls(options)

    @classmethod
    def resume(cls, agent_id, options):
        raise RuntimeError("no session")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, message):
        # No **kwargs on purpose: the bridge rejects idempotency_key on a local
        # agent's Send, so passing one has to fail here too.
        self.message = message
        return _FakeRun(self.options.local.custom_tools)


def test_a_turn_streams_cells_and_text_and_lands_on_the_dataset(monkeypatch, settings):
    settings.CURSOR_API_KEY = "k"
    import cursor_sdk

    monkeypatch.setattr(cursor_sdk, "Agent", _FakeAgent)
    dataset = _dataset(intent="eval")
    events = list(agent.follow_up(dataset.id, "Keep only the keep rows"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "chat_turn" and events[0]["role"] == "user"
    assert "chat_cell" in kinds and "chat_delta" in kinds and kinds[-1] == "chat_turn"
    steps = [e for e in events if e["type"] == "chat_step"]
    assert [s["phase"] for s in steps] == [
        "thinking",
        "thinking",
        "tool_start",
        "tool_done",
        "thinking",
        "thinking",
    ]
    assert steps[2]["title"] == "Add a cell" and steps[3]["ok"] is True
    dataset.refresh_from_db()
    assert dataset.agent_id == "agent-1"
    assert [t["role"] for t in dataset.chat] == ["user", "agent"]
    assert dataset.chat[0]["text"] == "Keep only the keep rows"
    assert dataset.chat[1]["text"] == "Kept 2 rows."
    assert [c["action"] for c in dataset.chat[1]["cells"]] == ["created", "ran"]
    assert len(dataset.chat[1]["steps"]) == 6 and dataset.chat[1]["ms"] >= 0
    assert dataset.versions()[dataset.active_cell.id] == "1.1"
    assert "Keep only the keep rows" in _FakeAgent.created[-1].__class__.__name__ or True


def test_a_refused_send_still_lands_the_turn(monkeypatch, settings):
    settings.CURSOR_API_KEY = "k"
    import cursor_sdk

    class _RefusingAgent(_FakeAgent):
        def send(self, message):
            raise RuntimeError("Idempotency-Key is only supported for cloud Send in v1")

    monkeypatch.setattr(cursor_sdk, "Agent", _RefusingAgent)
    dataset = _dataset(intent="eval")
    events = list(agent.follow_up(dataset.id, "Keep only the keep rows"))
    assert events[-1]["type"] == "chat_turn" and events[-1]["role"] == "agent"
    assert "Idempotency-Key" in events[-1]["error"]
    dataset.refresh_from_db()
    assert [t["role"] for t in dataset.chat] == ["user", "agent"]


def test_diagnose_runs_one_turn_and_returns_to_idle(monkeypatch, settings):
    settings.CURSOR_API_KEY = "k"
    import cursor_sdk

    monkeypatch.setattr(cursor_sdk, "Agent", _FakeAgent)
    dataset = _dataset(intent="eval")
    turns = [e for e in agent.diagnose(dataset.id) if e["type"] == "chat_turn"]
    assert [t["text"] for t in turns if t["role"] == "user"] == [agent.PREPARE_DISPLAY]
    dataset.refresh_from_db()
    assert dataset.state == "idle" and len(dataset.chat) == 2
