"""Spike MCP server: one read tool, one write tool, stateless streamable HTTP."""
import os
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

name = os.environ.get("SERVER_NAME", "spike")
mcp = MCPServer(name)


@mcp.tool()
def read_thing(key: str) -> str:
    """Read-only tool."""
    return f"{name}: read {key}"


@mcp.tool()
def write_thing(key: str, value: str) -> str:
    """Write tool, should be denied to viewer roles at the gateway."""
    return f"{name}: wrote {key}={value}"


app = mcp.streamable_http_app(
    stateless_http=True, host="0.0.0.0",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
