from urllib.parse import urlparse
from fastapi import HTTPException


def get_mcp_base_url(mcp_url: str) -> str:
    parsed_url = urlparse(mcp_url)
    return parsed_url.scheme + "://" + parsed_url.netloc + parsed_url.path


def _confirm_specific_url(
    mcp_base_url: str, mcp_url_policy: dict, action="raise"
) -> None:
    # Didn't implement non-raise action yet so we do nothing with it for now
    if mcp_url_policy["type"] == "whitelist":
        if (
            "*" not in mcp_url_policy["servers"]
            and mcp_base_url not in mcp_url_policy["servers"]
        ):
            raise HTTPException(
                status_code=401, detail=f"MCP URL is not whitelisted: {mcp_base_url}"
            )
    if mcp_url_policy["type"] == "blacklist":
        if (
            "*" in mcp_url_policy["servers"]
            or mcp_base_url in mcp_url_policy["servers"]
        ):
            raise HTTPException(
                status_code=401, detail=f"MCP URL is blacklisted: {mcp_base_url}"
            )


def validate_client_call_params(
    client_name: str, client_call_params: dict, mcp_url_policy: dict, action="raise"
) -> tuple[dict, dict]:
    """
    Will return validated (possibly filtered) client_call_params and list of approved MCP tool labels based on their URLS

    todo: we need to test this logic thoroughly especially the whitelisting/blacklisting and output formation
    """
    approved_mcp_label_tools_map = {}
    if client_name == "openai":
        for tool in client_call_params.get("tools", []):
            if tool.get("type") == "mcp":
                tool["require_approval"] = "always"
                mcp_url = tool.get("server_url")
                if mcp_url is None:
                    raise ValueError("MCP URL is required for MCP tools")
                mcp_base_url = get_mcp_base_url(mcp_url)

                _confirm_specific_url(
                    mcp_base_url=mcp_base_url,
                    mcp_url_policy=mcp_url_policy,
                    action=action,
                )

                if tool["server_label"] not in approved_mcp_label_tools_map:
                    approved_mcp_label_tools_map[tool["server_label"]] = (
                        mcp_url_policy.get(mcp_base_url, ["*"])
                    )
                else:
                    raise HTTPException(
                        status_code=404,
                        detail=f"MCP tool label is not unique: {tool['server_label']}",
                    )

        return client_call_params, approved_mcp_label_tools_map
    else:
        raise HTTPException(
            status_code=404, detail=f"Invalid client name: {client_name}"
        )
