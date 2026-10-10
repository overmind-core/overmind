import pytest


@pytest.fixture
def synced(cli, mcp_for, sample_agent, worker):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    return mcp_for(cli.project_key(sample_agent))


def test_a_scanned_agent_syncs_its_capabilities_and_checkout(synced, sample_agent):
    snapshot = synced.project()["repository_snapshot"]
    assert snapshot["commit"] == sample_agent.commit()
    assert snapshot["dirty"] is False
    assert snapshot["scanned_at"]

    for slug, truth in sample_agent.truth["capabilities"].items():
        capability = synced.capability(slug)
        assert capability["name"] == truth["name"]
        assert capability["model"] == truth["model"]
        assert capability["status"] == "current"


def test_a_capability_the_scan_misses_leaves_the_agent_and_returns_in_place(
    synced, cli, sample_agent
):
    answer = synced.capability("answer")

    cli.scan(sample_agent, without=("answer",))
    cli.sync(sample_agent)
    with pytest.raises(LookupError):
        synced.capability("answer")
    assert synced.capability("triage")["status"] == "current"

    cli.scan(sample_agent)
    cli.sync(sample_agent)
    returned = synced.capability("answer")
    assert returned["id"] == answer["id"]
    assert returned["status"] == "current"


def test_a_deleted_capability_is_hidden_and_a_later_scan_mints_a_new_one(
    synced, cli, rest_for, sample_agent
):
    answer = synced.capability("answer")
    rest_for(cli.project_key(sample_agent)).request("DELETE", f"/api/capabilities/{answer['id']}/")

    with pytest.raises(LookupError):
        synced.capability(answer["id"])

    cli.scan(sample_agent)
    cli.sync(sample_agent)
    assert synced.capability("answer")["id"] != answer["id"]
