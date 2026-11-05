from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from overmind.openrouter_rewrite import rewrite_repo

pytestmark = pytest.mark.covers("cli-contract")


@pytest.mark.parametrize(
    "source",
    [
        "import openai\nclient = openai.OpenAI()\n",
        "import openai as provider\nclient = provider.AsyncOpenAI()\n",
        "from anthropic import Anthropic, AsyncAnthropic\na = Anthropic()\nb = AsyncAnthropic()\n",
        "from openai import AsyncOpenAI\nfrom anthropic import Anthropic\na = Anthropic()\nb = AsyncOpenAI()\n",
    ],
)
def test_rewritten_constructors_resolve_all_client_names(tmp_path, monkeypatch, source):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=None, AsyncAnthropic=None))
    path = tmp_path / "agent.py"
    path.write_text(source)
    report = rewrite_repo(tmp_path)
    assert report.leftover == []
    namespace = {}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    clients = [namespace[name] for name in ("client", "a", "b") if name in namespace]
    assert all(str(client.base_url) == "https://openrouter.ai/api/v1/" for client in clients)
