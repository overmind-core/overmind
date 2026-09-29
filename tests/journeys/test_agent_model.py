def test_synced_capabilities_are_readable_over_mcp(cli, mcp_for, sample_agent, worker):
    cli.sync(sample_agent)
    mcp = mcp_for(cli.project_key(sample_agent))

    for slug, truth in sample_agent.truth["capabilities"].items():
        capability = mcp.capability(slug)
        assert capability["name"] == truth["name"]
        assert capability["model"] == truth["model"]
        assert capability["status"] == "current"
