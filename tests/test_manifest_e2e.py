"""E2E: kimm decorator surface → sync → graph/card/behaviours.

The platform venv installs PyPI ``overmind``; the decorator AST scan lives in
the in-repo SDK. Symbols below are the verified output of
``overmind.manifest.scan`` on ``kimm.py`` (see ``overmind/tests/test_manifest.py``).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from overbae.models import APIToken, Behaviour, Capability, Project, ProjectMembership, User

PROJECT_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

# Verified AgentManifest symbols from scanning kimm.py.
KIMM_SYMBOLS = [
    {
        "qualname": "kimm.wiki_search",
        "file": "kimm.py",
        "line_start": 1,
        "line_end": 20,
        "role": "tool",
        "capability": None,
        "slug": None,
        "name": "wiki_search",
        "description": "Search English Wikipedia for titles matching the query.",
        "signature": {
            "params": [{"name": "query", "type": "str", "required": True}],
            "returns": "str",
        },
        "calls": [],
        "expectations": [],
        "unresolved": [],
    },
    {
        "qualname": "kimm.wiki_read",
        "file": "kimm.py",
        "line_start": 21,
        "line_end": 40,
        "role": "tool",
        "capability": None,
        "slug": None,
        "name": "wiki_read",
        "description": "Read the lead summary of one Wikipedia page by exact title.",
        "signature": {
            "params": [{"name": "title", "type": "str", "required": True}],
            "returns": "str",
        },
        "calls": [],
        "expectations": [],
        "unresolved": [],
    },
    {
        "qualname": "kimm.llm",
        "file": "kimm.py",
        "line_start": 41,
        "line_end": 60,
        "role": "llm",
        "capability": None,
        "slug": None,
        "name": "llm",
        "description": "",
        "signature": {
            "params": [
                {"name": "messages", "type": "list[dict]", "required": True},
                {"name": "tools", "type": "", "required": False},
            ],
            "returns": "object",
        },
        "prompt_template": (
            "You research one query. Call wiki_search, then wiki_read on the best "
            "title (you may read a second page). Then stop calling tools and write "
            "3 to 5 factual bullets. Name the page title in each bullet."
        ),
        "expectations": [
            {"kind": "constraint", "spec": "names the Wikipedia page title in each bullet"}
        ],
        "calls": [],
        "unresolved": [],
    },
    {
        "qualname": "kimm.dig",
        "file": "kimm.py",
        "line_start": 61,
        "line_end": 90,
        "role": "function",
        "capability": None,
        "slug": None,
        "name": "dig",
        "description": "Research one Wikipedia query into factual bullets.",
        "signature": {
            "params": [{"name": "query", "type": "str", "required": True}],
            "returns": "str",
        },
        "calls": ["kimm.llm", "kimm.wiki_read", "kimm.wiki_search"],
        "expectations": [],
        "unresolved": [],
    },
    {
        "qualname": "kimm.dig",
        "file": "kimm.py",
        "line_start": 70,
        "line_end": 70,
        "role": "task",
        "capability": None,
        "slug": None,
        "name": "dig-query",
        "description": "",
        "signature": {"params": [], "returns": ""},
        "task_key": "dig-query",
        "unit": "turn",
        "calls": ["kimm.llm", "kimm.wiki_read", "kimm.wiki_search"],
        "expectations": [],
        "unresolved": [],
    },
    {
        "qualname": "kimm.research",
        "file": "kimm.py",
        "line_start": 100,
        "line_end": 140,
        "role": "capability",
        "capability": "research",
        "slug": "research",
        "name": "research",
        "description": "Answer a research question from Wikipedia evidence",
        "signature": {
            "params": [{"name": "question", "type": "str", "required": True}],
            "returns": "str",
        },
        "calls": ["kimm.dig", "kimm.llm", "kimm.wiki_read", "kimm.wiki_search"],
        "expectations": [],
        "unresolved": [],
    },
    {
        "qualname": "kimm.research",
        "file": "kimm.py",
        "line_start": 105,
        "line_end": 105,
        "role": "task",
        "capability": "research",
        "slug": None,
        "name": "research-run",
        "description": "",
        "signature": {"params": [], "returns": ""},
        "task_key": "research-run",
        "unit": "turn",
        "calls": ["kimm.dig", "kimm.llm", "kimm.wiki_read", "kimm.wiki_search"],
        "expectations": [],
        "unresolved": [],
    },
]

pytestmark = pytest.mark.django_db


@pytest.fixture
def project(db):
    return Project.objects.create(pk=PROJECT_ID, name="manifest-e2e", slug="manifest-e2e")


@pytest.fixture
def client(project):
    user = User.objects.create_user(
        email=f"{uuid.uuid4().hex}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    raw_key, _token = APIToken.create_for_user(user, project=project)
    c = APIClient()
    c.credentials(HTTP_X_API_KEY=raw_key)
    return c


@pytest.fixture(autouse=True)
def _quiet_side_effects():
    with (
        patch("overbae.services.agent_manifest.identity.enqueue_rebind"),
        patch(
            "overbae.services.agent_manifest.author_card",
            side_effect=lambda derived, evidence=None: {
                **derived,
                "task": derived.get("task") or "research a question",
                "domain": "research",
                "modality": "text",
            },
        ),
        patch("overbae.tasks.eval.preload_capability_eval_set.delay"),
    ):
        yield


def test_kimm_manifest_sync_graph_card_and_behaviours(client, project):
    body = {
        "project_id": str(project.id),
        "sdk_version": "0.3.0",
        "version": "0.3.0",
        "symbols": KIMM_SYMBOLS,
    }
    posted = client.post("/api/v1/sync", body, format="json")
    assert posted.status_code == 200, posted.data
    caps = posted.data["capabilities"]
    assert len(caps) == 1
    assert caps[0]["slug"] == "research"
    assert caps[0]["entrypoint_fn"] == "kimm.research"
    card = caps[0]["capability_card"]
    assert card.get("generator") == "derive_card@v1"
    assert "question" in (card.get("input_schema") or {})
    assert any(t.get("name") == "wiki_search" for t in (card.get("tool_spec") or []))

    cap = Capability.objects.get(project=project, slug="research")
    keys = set(Behaviour.objects.filter(capability=cap).values_list("key", flat=True))
    assert "research-run" in keys
    assert "dig-query" in keys

    graph = client.get("/api/agent/", {"project": str(project.id)}).data
    assert any(c["slug"] == "research" for c in graph["capabilities"])
    assert isinstance(graph.get("edges"), list)
    assert graph.get("last_synced_at")
