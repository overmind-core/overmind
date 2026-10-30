import pytest
import requests
from factories import make_project, make_user

from overbae.models import APIToken
from overbae.urls import router

from .stack import drain


@pytest.fixture
def secrets(cli, mcp_for, sample_agent, live_api, support_desk_llm, worker):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    sample_agent.run("Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    mcp = mcp_for(cli.project_key(sample_agent))
    [trace] = mcp.call("query_traces", {"limit": 5})["traces"]
    return {
        "project": mcp.project()["id"],
        "capability": mcp.capability("answer")["id"],
        "trace": trace["trace_id"],
    }


@pytest.fixture
def outsider():
    raw, _ = APIToken.create_for_user(make_user())
    return raw


@pytest.fixture
def outsider_project_key():
    user = make_user()
    raw, _ = APIToken.create_for_user(user, project=make_project(member=user))
    return raw


def list_routes() -> list[str]:
    return sorted({f"/api/{prefix}/" for prefix, _, _ in router.registry})


def test_no_list_endpoint_answers_without_credentials(secrets, live_api):
    open_routes = [
        route
        for route in list_routes()
        if requests.get(f"{live_api.url}{route}", timeout=30).status_code not in (401, 403)
    ]
    assert open_routes == []


def test_no_list_endpoint_shows_a_project_to_another_account(secrets, live_api, outsider):
    leaks = []
    for route in list_routes():
        response = requests.get(
            f"{live_api.url}{route}",
            params={"project": secrets["project"]},
            headers={"X-Api-Key": outsider},
            timeout=30,
        )
        if response.status_code >= 500:
            leaks.append((route, response.status_code))
        elif any(value in response.text for value in secrets.values()):
            leaks.append((route, "leak"))
    assert leaks == []


def test_another_account_cannot_read_the_project_over_mcp(secrets, mcp_for, outsider_project_key):
    mcp = mcp_for(outsider_project_key)
    for uri in (
        f"overmind://capabilities/{secrets['capability']}",
        f"overmind://traces/{secrets['trace']}",
    ):
        with pytest.raises(LookupError):
            mcp.read(uri)
