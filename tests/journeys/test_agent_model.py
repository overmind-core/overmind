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
