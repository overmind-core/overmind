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
    """Not entered as a context manager: the MCP session manager's lifespan can run only
    once per process."""
    return TestClient(application)


def first_match_allows(rules: str, path: str) -> bool:
    """A crawler that obeys the first rule matching the path, whatever its length."""
    for line in rules.splitlines():
        field, _, value = (part.strip() for part in line.partition(":"))
        if field.lower() in ("allow", "disallow") and value and path.startswith(value):
            return field.lower() == "allow"
    return True


def longest_match_allows(rules: str, path: str) -> bool:
    """Google's reading (RFC 9309): the longest matching rule wins, and Allow wins a tie."""
    best = (-1, True)
    for line in rules.splitlines():
        field, _, value = (part.strip() for part in line.partition(":"))
        if field.lower() in ("allow", "disallow") and value and path.startswith(value):
            best = max(best, (len(value), field.lower() == "allow"))
    return best[1]


@pytest.mark.parametrize("headers", [{}, {"X-Api-Key": "not-a-key"}])
def test_robots_txt_blocks_crawlers_but_not_well_known_without_authentication(client, headers):
    """A bad ``X-Api-Key`` must get the file too: DRF answers one with 401, even with AllowAny."""
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


@pytest.mark.parametrize("allows", [first_match_allows, longest_match_allows])
def test_first_and_longest_match_crawlers_read_the_rules_the_same_way(client, allows):
    """Google keeps the longest matching rule, simpler parsers the first one;
    ``RobotFileParser`` only became longest-match in Python 3.13.14."""
    rules = client.get("/robots.txt").text

    assert not any(allows(rules, path) for path in BLOCKED_PATHS)
    assert all(allows(rules, path) for path in FETCHABLE_PATHS)


def test_head_robots_txt_is_answered_like_get(client):
    """Fails if the view moves behind DRF's ``@api_view(["GET"])``, which answers HEAD with 405."""
    response = client.head("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
