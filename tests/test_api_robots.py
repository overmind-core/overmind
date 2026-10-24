"""The API host tells crawlers to stay out except /.well-known/, whatever credentials they send."""

from urllib.robotparser import RobotFileParser

import pytest
from starlette.testclient import TestClient

from overbae.asgi import application

API_URL = "https://api.overmindlab.ai"
BLOCKED_PATHS = ("/", "/api/capabilities/", "/api/docs/", "/health")
FETCHABLE_PATHS = (
    "/.well-known/oauth-authorization-server",
    "/.well-known/openai-apps-challenge",
)


@pytest.fixture
def client():
    # Not entered as a context manager: the MCP session manager's lifespan runs once per process.
    return TestClient(application)


def first_match_allows(rules: str, path: str) -> bool:
    """A crawler that obeys the first rule matching the path, whatever its length."""
    for line in rules.splitlines():
        field, _, value = (part.strip() for part in line.partition(":"))
        if field.lower() in ("allow", "disallow") and value and path.startswith(value):
            return field.lower() == "allow"
    return True


@pytest.mark.parametrize("headers", [{}, {"X-Api-Key": "not-a-key"}])
def test_robots_txt_blocks_crawlers_but_not_well_known_without_authentication(client, headers):
    response = client.get("/robots.txt", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    rules = RobotFileParser()
    rules.parse(response.text.splitlines())
    for agent in ("Googlebot", "bingbot", "SomeOtherBot"):
        for path in BLOCKED_PATHS:
            assert not rules.can_fetch(agent, f"{API_URL}{path}"), (agent, path)
        for path in FETCHABLE_PATHS:
            assert rules.can_fetch(agent, f"{API_URL}{path}"), (agent, path)


def test_a_first_match_crawler_reads_the_rules_the_same_way(client):
    rules = client.get("/robots.txt").text

    assert not any(first_match_allows(rules, path) for path in BLOCKED_PATHS)
    assert all(first_match_allows(rules, path) for path in FETCHABLE_PATHS)


def test_head_robots_txt_is_answered_like_get(client):
    response = client.head("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
