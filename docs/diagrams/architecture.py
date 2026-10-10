#!/usr/bin/env python3
"""Generates docs/img/architecture.svg. Shows what is deployed today.

    python3 docs/diagrams/architecture.py && python3 docs/diagrams/render.py docs/img/architecture.svg docs/img/architecture.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from archlib import Diagram  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "img", "architecture.svg")

W, H = 1300, 960
d = Diagram(W, H, "MCP gateway on GKE: what is deployed today",
            "agentgateway 1.6 · Keycloak (OAuth/OIDC IdP) · GKE Autopilot · MCP 2026-07-28 · JWT + role + per-tool RBAC")

d.group(190, 96, 1090, 520, "Google Cloud project · europe-west1", "#1a73e8", dash=False, fill="#f8faff", label_w=300)
d.group(215, 130, 1040, 462, "GKE Autopilot cluster  mcp-gw-eu  (Workload Identity, Dataplane V2, Shielded Nodes)", "#5f6368", dash=True, label_w=560)
d.group(240, 170, 230, 400, "ns agentgateway-system", "#8e44ad", dash=True, fill="#faf5fd", label_w=170)
d.group(500, 170, 730, 400, "ns mcp-platform  (Helm chart deploy/chart)", "#00897b", dash=True, fill="#f4fbfa", label_w=300)

d.node("cli", 90, 450, "MCP client", "user", "actor", "agent / tests/cli.py\nvia port-forward")
d.node("ctrl", 355, 330, "agentgateway\ncontrol plane", "policy", "compute", "fetches Keycloak's signing\nkeys, programs the proxy")
d.node("kc", 640, 270, "Keycloak", "key", "security", "IdP · realm mcp\nissues JWTs")
d.node("pol", 930, 225, "Policies", "shield", "security", "JWT: issuer, audience, signature\nrole check · per-tool RBAC (CEL)")
d.node("prx", 640, 450, "agentgateway\nproxy", "lb", "network", "Gateway + HTTPRoute\nserves discovery docs, routes /mcp")
d.node("a", 1120, 320, "mcp-alpha", "run", "compute", "read_thing\nwrite_thing")
d.node("b", 1120, 460, "mcp-beta", "run", "compute", "read_thing\nwrite_thing")

d.edge("cli", "prx", "h", label="steps 1, 2 and 4: MCP request, discovery, token", lab_dy=-10)
d.path([(90, 418), (90, 270), (608, 270)], num=3, label="client credentials", lab_at=(350, 262))
d.path([(672, 450), (850, 450), (850, 320), (1088, 320)], num=6, color="#d93025")
d.path([(850, 450), (850, 460), (1088, 460)], color="#d93025")
d.badge(850, 385, "5")
d.text(868, 440, "policy checked here", 11.5, "#d93025", "600")

d.legend(34, 640, "Request flow (every step asserted live by scripts/test.sh)", [
    ("1", "Client calls the gateway with no token: HTTP 401 + WWW-Authenticate: resource_metadata=<url>"),
    ("2", "Client reads the gateway's discovery documents (protected-resource metadata, authorization-server metadata)"),
    ("3", "Client authenticates at Keycloak (client-credentials) and receives a JWT: issuer, audience = this gateway, role"),
    ("4", "Client repeats the MCP request with Authorization: Bearer <JWT>"),
    ("5", "Gateway verifies signature, issuer, audience, expiry; needs a role (403 without); per-tool rule hides tools the role may not use"),
    ("6", "Allowed calls are routed to the server that owns the tool; tools/list is the merge of both servers"),
], w=1250)
d.text(34, 840, "Not built yet (planned): three real servers (k8s-ops, finops, tickets) · gateway-to-server service-to-service auth · Workload Identity per server ·", 12, "#b3261e", "600")
d.text(34, 858, "AI agent · NetworkPolicy default-deny · audit log + tracing · TLS and a private listener · Keycloak on a persistent database · human login (auth code + PKCE).", 12, "#b3261e", "600")
d.key(34, 880)

if __name__ == "__main__":
    d.save(OUT)
    print("wrote", os.path.abspath(OUT))
