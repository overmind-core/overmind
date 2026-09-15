"""Tests for openrouter_env, write_state and rewrite_repo."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from overmind.backtest import OPENROUTER_BASE_URL, openrouter_env, rewrite_repo, write_state


def test_openrouter_env_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        openrouter_env("openai/gpt-5-mini")


def test_openrouter_env_sets_model_and_base(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    env = openrouter_env("anthropic/claude-sonnet-4")
    assert env["OPENROUTER_MODEL"] == "anthropic/claude-sonnet-4"
    assert env["OPENAI_BASE_URL"] == OPENROUTER_BASE_URL
    assert env["OPENAI_API_KEY"] == "sk-test"


def test_write_state_merges(tmp_path):
    path = write_state(tmp_path, {"experiment_id": "aaa", "models": ["m1"]})
    write_state(tmp_path, {"models": ["m1", "m2"]})
    payload = json.loads(path.read_text())
    assert payload["experiment_id"] == "aaa"
    assert payload["models"] == ["m1", "m2"]


def test_rewrite_repo_openai_anthropic_env_and_leftover(tmp_path):
    (tmp_path / "client.py").write_text(
        "from openai import OpenAI\n"
        "client = OpenAI(api_key='sk')\n"
        "client.chat.completions.create(model='gpt-4', messages=[])\n"
    )
    (tmp_path / "anthropic_client.py").write_text(
        "from anthropic import Anthropic\n"
        "client = Anthropic(api_key='sk')\n"
        "client.messages.create(model='claude-3', max_tokens=8)\n"
    )
    (tmp_path / "chat.py").write_text(
        "from langchain_openai import ChatOpenAI\nllm = ChatOpenAI(model='gpt-4', temperature=0)\n"
    )
    (tmp_path / "leftover.py").write_text(
        "from langchain_anthropic import ChatAnthropic\nChatAnthropic(model='claude')\n"
    )
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "hidden.py").write_text("from openai import OpenAI\nOpenAI()\n")
    (tmp_path / ".env").write_text("FOO=bar\n")

    report = rewrite_repo(tmp_path)

    openai_src = (tmp_path / "client.py").read_text()
    assert "openrouter.ai" in openai_src
    assert "OPENROUTER_API_KEY" in openai_src
    assert "OPENROUTER_MODEL" in openai_src
    assert "import os" in openai_src

    anthropic_src = (tmp_path / "anthropic_client.py").read_text()
    assert "from openai import OpenAI" in anthropic_src
    assert "chat.completions.create" in anthropic_src
    assert ".messages.create(" not in anthropic_src
    assert "OPENROUTER_API_KEY" in anthropic_src

    chat_src = (tmp_path / "chat.py").read_text()
    assert "openai_api_base" in chat_src
    assert "OPENROUTER_MODEL" in chat_src

    leftover_src = (tmp_path / "leftover.py").read_text()
    assert "ChatAnthropic" in leftover_src
    assert any("leftover.py" in item for item in report.leftover)

    assert (venv / "hidden.py").read_text() == "from openai import OpenAI\nOpenAI()\n"
    env_src = (tmp_path / ".env").read_text()
    assert "OPENAI_BASE_URL=" in env_src
    assert "OPENAI_API_BASE=" in env_src
    assert ".env" in report.env_files
    assert "client.py" in report.changed


def test_rewritten_entrypoint_routes_openrouter_per_model(tmp_path, monkeypatch):
    """The skill's runtime path: rewrite, set openrouter_env per model, run the
    repo's own entrypoint per datapoint — every call must hit OpenRouter with
    the env-selected model."""
    (tmp_path / "agent.py").write_text(
        "from openai import OpenAI\n"
        "\n"
        "def run(question):\n"
        "    client = OpenAI(api_key='sk-original')\n"
        "    resp = client.chat.completions.create(\n"
        "        model='gpt-4o', messages=[{'role': 'user', 'content': question}]\n"
        "    )\n"
        "    return resp.choices[0].message.content\n"
    )
    rewrite_repo(tmp_path)

    ctors: list[dict] = []
    calls: list[dict] = []

    class _StubOpenAI:
        def __init__(self, **kwargs):
            ctors.append(kwargs)
            create = self._create
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        @staticmethod
        def _create(**kwargs):
            calls.append(kwargs)
            message = SimpleNamespace(content=f"echo:{kwargs['model']}")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    stub = ModuleType("openai")
    stub.OpenAI = _StubOpenAI
    stub.AsyncOpenAI = _StubOpenAI
    monkeypatch.setitem(sys.modules, "openai", stub)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    datapoints = ["hello", "bye"]
    results = []
    models = ["openai/gpt-5-mini", "anthropic/claude-sonnet-4"]
    for order, model in enumerate(models):
        for key, value in openrouter_env(model).items():
            monkeypatch.setenv(key, value)
        spec = importlib.util.spec_from_file_location(f"agent_{order}", tmp_path / "agent.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for index, question in enumerate(datapoints):
            output = module.run(question)
            results.append({"order": order, "datapoint_index": index, "success": True, "output": output})

    assert len(ctors) == len(models) * len(datapoints)
    assert all(c["base_url"] == OPENROUTER_BASE_URL for c in ctors)
    assert all(c["api_key"] == "sk-or-test" for c in ctors)
    assert [c["model"] for c in calls] == [m for m in models for _ in datapoints]
    assert [r["output"] for r in results] == [f"echo:{m}" for m in models for _ in datapoints]


def test_rewrite_repo_keeps_future_import_first(tmp_path):
    (tmp_path / "mod.py").write_text(
        "#!/usr/bin/env python3\n"
        '"""client."""\n'
        "from __future__ import annotations\n"
        "from openai import OpenAI\n"
        "OpenAI(api_key='sk')\n"
    )
    rewrite_repo(tmp_path)
    src = (tmp_path / "mod.py").read_text()
    tree = ast.parse(src)
    assert src.splitlines()[0].startswith("#!")
    assert "import os" in src
    future = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "__future__")
    os_import = next(n for n in tree.body if isinstance(n, ast.Import) and any(a.name == "os" for a in n.names))
    assert future.lineno < os_import.lineno
