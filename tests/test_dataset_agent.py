"""The notebook agent: its tools over a real chain, the prompt both engines
read, a turn driven through a fake Cursor session, and a turn driven through a
fake native stream."""

from __future__ import annotations

import json
import types
import uuid

import pytest

from overbae.core.llms import ToolStreamDelta, ToolStreamResult
from overbae.core.model_registry import WORKSHOP_KEY_ENVS
from overbae.models import Capability, Dataset, Project
from overbae.services.datasets import land, lifecycle, paths
from overbae.services.datasets.notebook import agent, engines, prompts, workspace
from overbae.services.datasets.notebook.engines import native

pytestmark = pytest.mark.django_db

ROWS = [
    {"question": "q1", "answer": "a1", "tag": "keep"},
    {"question": "", "answer": "orphan", "tag": "junk"},
    {"question": "q3", "answer": "a3", "tag": "keep"},
]
KEEP = "df = df[df['tag'] == 'keep']\n"
SHAPE = "df = df.rename(columns={'question': 'input', 'answer': 'expected_output'})\n"


@pytest.fixture(autouse=True)
def no_engine_keys(monkeypatch):
    for env in WORKSHOP_KEY_ENVS:
        monkeypatch.delenv(env, raising=False)


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


def test_a_cell_that_nests_a_missing_value_writes_valid_json():
    dataset = _dataset(intent="eval")
    tools, _ = _tools(dataset)
    script = (
        "df['payload'] = df.apply(lambda r: {'q': r['question'] or None, "
        "'n': float('nan')}, axis=1)\n"
    )
    result = tools.add_cell({"title": "Nest", "script": script})
    assert result["ok"] and result["rows"] == 3
    rows = tools.query({"sql": "SELECT payload FROM t ORDER BY question"})["rows"]
    assert all(json.dumps(r) for r in rows)
    assert {
        json.loads(r["payload"])["n"] if isinstance(r["payload"], str) else r["payload"]["n"]
        for r in rows
    } == {None}


def test_try_script_previews_without_landing():
    dataset = _dataset()
    tools, events = _tools(dataset)
    result = tools.try_script({"script": KEEP})
    assert result["ok"] and result["rows"] == 2 and "source_row" not in result["columns"]
    assert "source_row" not in result["head"][0]
    assert dataset.cells.count() == 1 and events == []
    bad = tools.try_script({"script": "df = df['nope']\n"})
    assert bad["ok"] is False and "nope" in bad["error"]


def test_inspect_reads_back_what_the_script_printed_and_lands_nothing():
    dataset = _dataset()
    tools, events = _tools(dataset)
    result = tools.inspect({"script": "print('keepers', (df['tag'] == 'keep').sum())\n"})
    assert result["ok"] and "keepers 2" in result["stdout"]
    assert dataset.cells.count() == 1 and events == []
    assert tools.inspect({"script": "total = len(df)\n"})["ok"] is True
    bad = tools.inspect({"script": "print(df['nope'])\n"})
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
    handlers = tools.handlers()
    assert (
        set(handlers)
        == set(agent.TOOL_SPECS)
        == {
            "status",
            "query",
            "diff",
            "try_script",
            "inspect",
            "add_cell",
            "edit_cell",
            "remove_cell",
            "set_active",
            "set_intent",
            "set_capability",
            "rename",
            "install",
        }
    )
    out = handlers["query"]({"sql": "SELECT 1.5::DECIMAL(4,2) AS d FROM t LIMIT 1"})
    assert out["rows"] == [{"d": "1.50"}]
    failed = handlers["add_cell"]({"title": "x", "script": "df = df['nope']\n"})
    assert failed["ok"] is False and "nope" in failed["error"]
    phases = [(s["phase"], s.get("tool"), s.get("ok")) for s in tools.steps if "tool" in s]
    assert phases == [
        ("tool_start", "query", None),
        ("tool_done", "query", True),
        ("tool_start", "add_cell", None),
        ("tool_done", "add_cell", False),
    ]
    assert tools.steps[-2]["preview"].startswith('{"ok": false')


def test_tool_schemas_come_from_the_one_table():
    schemas = agent.tool_schemas()
    assert [s["function"]["name"] for s in schemas] == list(agent.TOOL_SPECS)
    assert schemas[1]["function"]["parameters"]["required"] == ["sql"]
    assert set(agent.TOOL_TITLES) == set(agent.TOOL_SPECS)


def test_system_prompt_carries_playbook_capability_libraries_and_sample():
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
    dataset.refresh_from_db()
    text = agent.system_prompt(dataset)
    assert prompts.WORKSHOP.splitlines()[0] in text and "## Eval playbook" in text
    assert "Capability: KB" in text and '"system_prompt": "You are KB."' in text
    assert '"name": "search"' in text
    assert "## Libraries" in text and "pandas" in text
    assert "## Source sample" in text and "source_row" not in text


def test_status_carries_each_cell_script_so_the_agent_can_edit_it():
    dataset = _dataset(intent="eval")
    lifecycle.add_cell(dataset, title="Keep rows", script=KEEP)
    dataset.refresh_from_db()
    tools, _ = _tools(dataset)
    assert tools.status({})["cells"][1]["script"] == KEEP


def test_workspace_carries_prompt_cells_and_frames():
    dataset = _dataset(intent="eval")
    lifecycle.add_cell(dataset, title="Keep rows", script=KEEP)
    root = workspace.prepare(dataset, agent.system_prompt(dataset))
    text = (root / "AGENTS.md").read_text()
    assert prompts.WORKSHOP.splitlines()[0] in text and "## Workspace" in text
    assert (
        (root / "cells" / "001_keep_rows.py").read_text().startswith("# 1.1 · Keep rows · queued")
    )
    assert (root / "frames" / "1.0.parquet").resolve() == paths.cell_path(
        dataset.id, dataset.source.id
    )
    assert not (root / "sample.jsonl").exists()


# ─── The Cursor engine ────────────────────────────────────────────────────────


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

    usage = {"inputTokens": 100, "outputTokens": 20, "cacheReadTokens": 60}


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


@pytest.fixture
def cursor(monkeypatch):
    import cursor_sdk

    monkeypatch.setenv("CURSOR_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "shadowed")
    monkeypatch.setattr(cursor_sdk, "Agent", _FakeAgent)
    return _FakeAgent


def test_a_cursor_turn_streams_cells_and_text_and_lands_on_the_dataset(cursor):
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
    assert dataset.agent_id == "agent-1" and dataset.agent_messages == []
    assert [t["role"] for t in dataset.chat] == ["user", "agent"]
    assert dataset.chat[1]["text"] == "Kept 2 rows."
    assert dataset.chat[1]["engine"] == "cursor" and dataset.chat[1]["model"] == "composer-2.5"
    assert [c["action"] for c in dataset.chat[1]["cells"]] == ["created", "ran"]
    assert len(dataset.chat[1]["steps"]) == 6 and dataset.chat[1]["ms"] >= 0
    assert dataset.versions()[dataset.active_cell.id] == "1.1"
    assert cursor.created[-1].model == "composer-2.5"
    assert set(cursor.created[-1].local.custom_tools) == set(agent.TOOL_SPECS)


def test_a_refused_cursor_send_still_lands_the_turn(cursor, monkeypatch):
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
    assert [t["role"] for t in dataset.chat] == ["user", "agent"] and dataset.state == "idle"


def test_a_cursor_turn_bills_composer_through_the_registry(cursor, monkeypatch):
    from django.contrib.auth import get_user_model

    charged = {}
    monkeypatch.setattr(
        "overbae.services.billing_ledger.charge_llm_usage",
        lambda user, stats, **kw: charged.update(stats=stats, **kw),
    )
    user = get_user_model().objects.create_user(
        email="ws@example.com", password="x", clerk_user_id="clerk_ws"
    )
    dataset = _dataset(intent="eval")
    list(agent.follow_up(dataset.id, "Keep only the keep rows", user=user))
    assert charged["stats"] == {
        "prompt_tokens": 160,
        "completion_tokens": 20,
        "cached_tokens": 60,
        "served_model": "composer-2.5",
    }
    assert charged["service"] == "data-workshop"
    assert charged["metadata"]["engine"] == "cursor"


# ─── The native engine ────────────────────────────────────────────────────────


def _fake_stream(tool_calls_then_text, *, reasoning=""):
    """Stand in for ``stream_llm_tools``: yield deltas, then the result.

    ``tool_calls_then_text`` is one entry per round.
    """
    rounds = iter(tool_calls_then_text)

    def _stream(messages, schemas, **_kwargs):
        calls, text = next(rounds)
        if reasoning:
            yield ToolStreamDelta("reasoning", reasoning)
        for token in text:
            yield ToolStreamDelta("text", token)
        yield ToolStreamResult(
            "".join(text),
            calls,
            {"prompt_tokens": 10, "completion_tokens": 2, "response_cost": 0.01},
            reasoning,
            [{"type": "reasoning.text", "text": reasoning}] if reasoning else [],
        )

    return _stream


def _call(name, args):
    return {
        "id": f"call-{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


@pytest.fixture
def openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")


def test_a_native_turn_streams_cells_and_text_and_lands_on_the_dataset(openrouter, monkeypatch):
    """The step sequence the chat renders is the same one the Cursor engine
    produces, so it is pinned exactly."""
    monkeypatch.setattr(
        native,
        "stream_llm_tools",
        _fake_stream(
            [
                ([_call("add_cell", {"title": "Keep", "script": KEEP})], ""),
                ([], "Kept 2 rows."),
            ]
        ),
    )
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
    assert dataset.agent_id == ""
    assert [t["role"] for t in dataset.chat] == ["user", "agent"]
    assert dataset.chat[1]["text"] == "Kept 2 rows."
    assert dataset.chat[1]["engine"] == "openrouter"
    assert [c["action"] for c in dataset.chat[1]["cells"]] == ["created", "ran"]
    assert len(dataset.chat[1]["steps"]) == 6 and dataset.chat[1]["ms"] >= 0
    assert dataset.versions()[dataset.active_cell.id] == "1.1"


def test_the_ladder_picks_cursor_then_openrouter_then_a_vendor_key(monkeypatch):
    seen: list[dict] = []
    inner = _fake_stream([([], "Hi.")] * 3)

    def _spy(messages, schemas, **kwargs):
        seen.append(kwargs)
        yield from inner(messages, schemas, **kwargs)

    monkeypatch.setattr(native, "stream_llm_tools", _spy)
    dataset = _dataset(intent="eval")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    list(agent.follow_up(dataset.id, "one"))
    assert seen[-1]["provider"].name == "anthropic" and seen[-1]["model"] == "claude-sonnet-5"
    assert seen[-1]["fallback_models"] == ["claude-sonnet-5"]

    monkeypatch.setenv("OPENAI_API_KEY", "o")
    list(agent.follow_up(dataset.id, "two"))
    assert seen[-1]["provider"].name == "openai" and seen[-1]["model"] == "gpt-5.6-terra"

    monkeypatch.setenv("OPENROUTER_API_KEY", "r")
    list(agent.follow_up(dataset.id, "three"))
    assert seen[-1]["provider"].name == "openrouter"
    assert seen[-1]["fallback_models"] == [
        "gpt-5.6-terra",
        "claude-sonnet-5",
        "gemini-3.1-pro-preview",
    ]
    dataset.refresh_from_db()
    assert [t["engine"] for t in dataset.chat if t["role"] == "agent"] == [
        "anthropic",
        "openai",
        "openrouter",
    ]


def test_no_key_lands_an_error_turn_and_frees_the_dataset():
    dataset = _dataset(intent="eval")
    events = list(agent.follow_up(dataset.id, "Hello?"))
    assert events[-1]["type"] == "chat_turn" and events[-1]["error"] == engines.NOT_CONFIGURED
    assert "CURSOR_API_KEY" in engines.NOT_CONFIGURED
    dataset.refresh_from_db()
    assert dataset.state == "idle" and dataset.chat[1]["engine"] == ""


def test_text_deltas_are_coalesced_not_one_event_per_token(openrouter, monkeypatch):
    """One event per token is one Redis publish and one markdown re-parse each.
    The reader still sees the whole text, in order."""
    tokens = [f"w{i} " for i in range(200)]
    monkeypatch.setattr(native, "stream_llm_tools", _fake_stream([([], tokens)]))
    dataset = _dataset(intent="eval")
    events = list(agent.follow_up(dataset.id, "Say something long"))
    deltas = [e for e in events if e["type"] == "chat_delta"]
    assert 0 < len(deltas) < len(tokens)
    assert "".join(d["text"] for d in deltas) == "".join(tokens)
    dataset.refresh_from_db()
    assert dataset.chat[1]["text"] == "".join(tokens).strip()

    # The flush is driven by elapsed time, not by the end of the stream: with no
    # interval every token goes out on its own, so nothing is held back.
    monkeypatch.setattr(native, "DELTA_FLUSH_SECONDS", 0)
    monkeypatch.setattr(native, "stream_llm_tools", _fake_stream([([], tokens)]))
    other = _dataset(intent="eval")
    eager = [e for e in agent.follow_up(other.id, "again") if e["type"] == "chat_delta"]
    assert len(eager) == len(tokens)


def test_a_native_turn_keeps_the_tool_exchange_for_the_next_turn(openrouter, monkeypatch):
    monkeypatch.setattr(
        native,
        "stream_llm_tools",
        _fake_stream([([_call("status", {})], ""), ([], "Three rows.")]),
    )
    dataset = _dataset(intent="eval")
    list(agent.follow_up(dataset.id, "How many rows?"))
    dataset.refresh_from_db()
    roles = [m["role"] for m in dataset.agent_messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert dataset.agent_messages[1]["tool_calls"][0]["function"]["name"] == "status"


def test_a_long_turn_keeps_its_instructions(openrouter, monkeypatch):
    """The system prompt is outside the context budget. Preparing a table runs to
    dozens of tool calls, and an agent that loses its playbook half way through
    reports what it managed instead of what the table needs."""
    monkeypatch.setattr(native, "CONTEXT_CHARS", 2000)
    monkeypatch.setattr(native, "COMPACT_RESULT_CHARS", 100)
    seen: list[list[dict]] = []

    rounds = [([_call("status", {})], "")] * 6 + [([], "Done.")]
    inner = _fake_stream(rounds)

    def _spy(messages, schemas, **kwargs):
        seen.append(list(messages))
        yield from inner(messages, schemas, **kwargs)

    monkeypatch.setattr(native, "stream_llm_tools", _spy)
    dataset = _dataset(intent="eval")
    list(agent.follow_up(dataset.id, "Prepare it"))

    assert len(seen) == 7
    for sent in seen:
        assert sent[0]["role"] == "system"
        assert "# Data Workshop" in sent[0]["content"][0]["text"]
        assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    # The turn in flight keeps its own user message however tight the budget.
    assert seen[-1][1]["role"] == "user" and "Prepare it" in seen[-1][1]["content"]
    # Old results are compacted, not dropped: every step the agent took is still there.
    assert [m["role"] for m in seen[-1][2:]].count("tool") == 6
    assert "compacted" in seen[-1][3]["content"]


def test_fit_compacts_old_results_before_dropping_rounds():
    big = "x" * 1000
    exchange = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": None, "tool_calls": [_call("status", {})]},
        {"role": "tool", "tool_call_id": "call-status", "content": big},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": None, "tool_calls": [_call("query", {})]},
        {"role": "tool", "tool_call_id": "call-query", "content": big},
    ]
    kept = native.fit(exchange, keep_from=4, budget=native.COMPACT_RESULT_CHARS * 2 + 600)
    assert [m["role"] for m in kept] == [m["role"] for m in exchange]
    assert all(len(m["content"]) < 1000 for m in kept if m["role"] == "tool")
    # A round is dropped whole, never leaving a tool result without its call.
    tight = native.fit(exchange, keep_from=4, budget=800)
    assert tight[0]["role"] == "user" and tight[0]["content"] == "second"
    assert len(tight) == 3


def test_a_tool_result_keeps_every_row_and_cuts_structurally():
    rows = [{"n": i, "text": "t" * 100} for i in range(50)]
    whole = json.loads(native.result_payload({"rows": rows, "columns": ["n", "text"]}))
    assert len(whole["rows"]) == 50
    huge = [{"n": i, "text": "t" * 1000} for i in range(50)]
    cut = json.loads(native.result_payload({"rows": huge, "columns": ["n", "text"]}))
    assert 0 < len(cut["rows"]) < 50 and cut["columns"] == ["n", "text"]
    assert cut["truncated"].startswith("rows:")


def test_a_tool_step_is_published_before_the_tool_returns(openrouter, monkeypatch):
    """A cell run takes minutes. The page must see the step start when it
    starts, not when the result is in."""
    published: list[str] = []
    monkeypatch.setattr(
        agent.events, "publish", lambda _id, event, **_kw: published.append(event["type"])
    )
    slow_calls: list[str] = []
    original = agent.Tools.status

    def _slow_status(self, args, _ctx=None):
        slow_calls.append("running:" + ",".join(published[-2:]))
        return original(self, args, _ctx)

    monkeypatch.setattr(agent.Tools, "status", _slow_status)
    monkeypatch.setattr(
        native,
        "stream_llm_tools",
        _fake_stream([([_call("status", {})], ""), ([], "Three rows.")]),
    )
    dataset = _dataset(intent="eval")
    list(agent.follow_up(dataset.id, "How many rows?"))
    assert slow_calls == ["running:chat_step,chat_step"]


def test_reasoning_lands_on_the_thinking_step_and_rides_the_next_request(openrouter, monkeypatch):
    seen: list[list[dict]] = []
    inner = _fake_stream(
        [([_call("status", {})], ""), ([], "Three rows.")], reasoning="Count the rows."
    )

    def _spy(messages, schemas, **kwargs):
        seen.append(list(messages))
        yield from inner(messages, schemas, **kwargs)

    monkeypatch.setattr(native, "stream_llm_tools", _spy)
    dataset = _dataset(intent="eval")
    events = list(agent.follow_up(dataset.id, "How many rows?"))
    thinking = [e for e in events if e["type"] == "chat_thinking"]
    assert thinking and thinking[0]["text"] == "Count the rows."
    done = [e for e in events if e["type"] == "chat_step" and e.get("status") == "done"]
    assert done[0]["text"] == "Count the rows."
    assert seen[1][2]["reasoning_details"] == [
        {"type": "reasoning.text", "text": "Count the rows."}
    ]
    dataset.refresh_from_db()
    # Provider-bound blocks do not outlive the turn.
    assert all("reasoning_details" not in m for m in dataset.agent_messages)


def test_a_dropped_stream_is_retried_when_nothing_reached_the_reader(openrouter, monkeypatch):
    attempts: list[int] = []
    inner = _fake_stream([([], "Fine.")])

    def _flaky(messages, schemas, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Error streaming LLM: connection reset")
        yield from inner(messages, schemas, **kwargs)

    monkeypatch.setattr(native, "stream_llm_tools", _flaky)
    dataset = _dataset(intent="eval")
    list(agent.follow_up(dataset.id, "Hi"))
    dataset.refresh_from_db()
    assert (
        len(attempts) == 2 and dataset.chat[1]["text"] == "Fine." and not dataset.chat[1]["error"]
    )


def test_a_rejected_key_is_named_in_the_turn(openrouter, monkeypatch):
    import httpx
    import openai

    response = httpx.Response(401, request=httpx.Request("POST", "https://x"))

    def _refused(messages, schemas, **kwargs):
        raise RuntimeError("Error calling LLM") from openai.AuthenticationError(
            "bad key", response=response, body=None
        )
        yield  # pragma: no cover

    monkeypatch.setattr(native, "stream_llm_tools", _refused)
    dataset = _dataset(intent="eval")
    events = list(agent.follow_up(dataset.id, "Hi"))
    assert events[-1]["error"] == "The model provider rejected this server's OPENROUTER_API_KEY."


def test_a_redelivered_turn_does_not_run_its_tools_twice(openrouter, monkeypatch):
    """``tasks.datasets.turn`` is acks_late, so a worker lost mid-turn has the
    same message redelivered under the same task id. The key makes it a no-op."""
    monkeypatch.setattr(
        native,
        "stream_llm_tools",
        _fake_stream(
            [
                ([_call("add_cell", {"title": "Keep", "script": KEEP})], ""),
                ([], "Kept 2 rows."),
            ]
        ),
    )
    dataset = _dataset(intent="eval")
    assert list(agent.follow_up(dataset.id, "Keep the keep rows", turn_key="task-1"))
    assert list(agent.follow_up(dataset.id, "Keep the keep rows", turn_key="task-1")) == []
    dataset.refresh_from_db()
    assert dataset.cells.count() == 2
    assert [t["role"] for t in dataset.chat] == ["user", "agent"]


def test_a_turn_that_runs_out_of_rounds_still_reports(openrouter, monkeypatch):
    """The last completion carries no tools: the agent can only write."""
    monkeypatch.setattr(native, "MAX_ROUNDS", 2)
    seen: list[list] = []
    inner = _fake_stream([([_call("status", {})], "")] * 2 + [([], "Read the chain twice.")])

    def _spy(messages, schemas, **kwargs):
        seen.append(schemas)
        yield from inner(messages, schemas, **kwargs)

    monkeypatch.setattr(native, "stream_llm_tools", _spy)
    dataset = _dataset(intent="eval")
    list(agent.follow_up(dataset.id, "Loop forever"))
    dataset.refresh_from_db()
    assert dataset.chat[1]["error"] == "The agent used its whole tool budget for this turn."
    assert dataset.chat[1]["text"] == "Read the chain twice."
    assert seen[-1] == [] and seen[0] != []


def test_a_turn_holds_the_dataset_and_a_failed_chain_settles_as_error(openrouter, monkeypatch):
    seen_states: list[str] = []
    original = agent.Tools.status

    def _status(self, args, _ctx=None):
        seen_states.append(Dataset.objects.get(pk=self.dataset_id).state)
        return original(self, args, _ctx)

    monkeypatch.setattr(agent.Tools, "status", _status)
    monkeypatch.setattr(
        native,
        "stream_llm_tools",
        _fake_stream(
            [
                ([_call("add_cell", {"title": "Bad", "script": "df = df['nope']\n"})], ""),
                ([_call("status", {})], ""),
                ([], "That failed."),
            ]
        ),
    )
    dataset = _dataset(intent="eval")
    list(agent.follow_up(dataset.id, "Break it"))
    # The failed run inside the turn did not drop the dataset out of the turn.
    assert seen_states == ["diagnosing"]
    dataset.refresh_from_db()
    assert dataset.state == "error" and "Bad" in dataset.error
    # A follow-up from the error state is allowed and repairs it.
    monkeypatch.setattr(
        native,
        "stream_llm_tools",
        _fake_stream([([_call("remove_cell", {"version": "1"})], ""), ([], "Removed.")]),
    )
    list(agent.follow_up(dataset.id, "Remove it"))
    dataset.refresh_from_db()
    assert dataset.state == "idle" and dataset.error == ""


def test_diagnose_runs_one_turn_and_returns_to_idle(openrouter, monkeypatch):
    monkeypatch.setattr(native, "stream_llm_tools", _fake_stream([([], "Nothing to change.")]))
    dataset = _dataset(intent="eval")
    turns = [e for e in agent.diagnose(dataset.id) if e["type"] == "chat_turn"]
    assert [t["text"] for t in turns if t["role"] == "user"] == [agent.PREPARE_DISPLAY]
    dataset.refresh_from_db()
    assert dataset.state == "idle" and len(dataset.chat) == 2
