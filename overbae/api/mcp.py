"""ASGI application for the public MCP endpoint."""

from overbae.services.mcp.server import create_mcp_application

mcp_application = create_mcp_application()
