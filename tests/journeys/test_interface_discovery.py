def test_account_discovery_agrees_with_the_connected_contract(account_key, mcp_for):
    mcp = mcp_for(account_key)
    discovery = mcp.discover()
    interface = mcp.read("overmind://interface/current")
    assert interface["contract_version"] == discovery["version"]
    assert interface["tool_count"] == len(discovery["tools"])
    assert "resume_dataset_import" in discovery["tools"]
