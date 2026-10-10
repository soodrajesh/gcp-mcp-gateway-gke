"""Entry point: SERVER=k8s|finops|tickets selects which MCP server this container runs."""
from __future__ import annotations

import importlib
import os

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from mcpkit.asgi import RequireGatewayIdentity
from mcpkit.auth import GatewayIdentityVerifier

NAME = os.environ["SERVER"]                                   # k8s | finops | tickets
NAMESPACE = os.environ.get("NAMESPACE", "mcp-platform")
AUDIENCE = os.environ.get("AUDIENCE", f"mcp-{NAME}")           # must equal the audience the gateway asks Google for
GATEWAY_EMAIL = os.environ["GATEWAY_EMAIL"]                    # the only caller allowed in

mcp = importlib.import_module(f"servers.{NAME}").mcp
host = f"mcp-{NAME}.{NAMESPACE}.svc.cluster.local"
inner = mcp.streamable_http_app(
    stateless_http=True, host="0.0.0.0",
    # Host-header allow-list (DNS-rebinding protection) instead of switching the protection off.
    transport_security=TransportSecuritySettings(allowed_hosts=[f"{host}:8000", host, f"mcp-{NAME}:8000", f"mcp-{NAME}"]),
)
app = RequireGatewayIdentity(inner, GatewayIdentityVerifier(AUDIENCE, {GATEWAY_EMAIL}), NAME)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
