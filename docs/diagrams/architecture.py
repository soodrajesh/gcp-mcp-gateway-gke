#!/usr/bin/env python3
"""Generates docs/img/architecture.svg. Shows what the spike actually deploys today.

    python3 docs/diagrams/architecture.py && python3 docs/diagrams/render.py docs/img/architecture.svg docs/img/architecture.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from archlib import Diagram  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "img", "architecture.svg")

W, H = 1180, 900
d = Diagram(W, H, "MCP gateway on GKE: what is deployed today (spike)",
            "agentgateway 1.6 · GKE Autopilot · two stateless MCP servers · JWT auth · per-tool RBAC (CEL)")

d.group(190, 96, 970, 470, "Google Cloud project · europe-west1", "#1a73e8", dash=False, fill="#f8faff", label_w=300)
d.group(215, 130, 925, 418, "GKE Autopilot cluster  mcp-gw-eu  (Workload Identity, Dataplane V2, Shielded Nodes)", "#5f6368", dash=True, label_w=560)
d.group(240, 170, 440, 360, "namespace agentgateway-system", "#8e44ad", dash=True, fill="#faf5fd", label_w=250)
d.group(720, 170, 395, 360, "namespace spike", "#00897b", dash=True, fill="#f4fbfa", label_w=170)

d.node("cli", 90, 420, "MCP client", "user", "actor", "test script via\nkubectl port-forward")
d.node("ctrl", 340, 250, "agentgateway\ncontrol plane", "policy", "compute", "reads Gateway, HTTPRoute,\nBackend, Policy objects")
d.node("pol", 570, 250, "AgentgatewayPolicy", "shield", "security", "JWT: issuer, audience,\nexpiry, signature\nRBAC: role -> tools (CEL)")
d.node("prx", 340, 420, "agentgateway\nproxy", "lb", "network", "Gateway + HTTPRoute /mcp\nfederates both servers")
d.node("a", 880, 290, "mcp-alpha", "run", "compute", "read_thing\nwrite_thing")
d.node("b", 880, 440, "mcp-beta", "run", "compute", "read_thing\nwrite_thing")

d.edge("cli", "prx", "h", num=1, label="Bearer JWT")
d.edge("ctrl", "pol", "h", label="reads", dash=True)
d.path([(372, 420), (640, 420), (640, 290), (848, 290)], num=2, label="policy enforced here", lab_at=(560, 412), color="#d93025")
d.path([(640, 420), (640, 440), (848, 440)], color="#d93025")
d.badge(740, 290, "3")
d.text(190, 590, "The control plane programs the proxy (xDS). The policy is checked on every request before it is forwarded.", 11.5)

d.legend(34, 612, "Request flow (verified live by scripts/test.sh)", [
    ("1", "Client sends an MCP request with a bearer token to the gateway (no token / bad token -> HTTP 401)"),
    ("2", "Proxy validates the JWT, then evaluates the role claim against the tool the caller asked for"),
    ("3", "Allowed calls fan out to the right server; tools/list merges both servers; denied tools are hidden"),
], w=1110)
d.text(34, 744, "Not built yet (planned): Keycloak as the issuer · MCP protected-resource metadata · Workload Identity per server ·", 12, "#b3261e", "600")
d.text(34, 762, "k8s-ops / finops / tickets servers · AI agent · NetworkPolicy default-deny · audit log + tracing · private VPC and Cloud NAT.", 12, "#b3261e", "600")
d.key(34, 786)

if __name__ == "__main__":
    d.save(OUT)
    print("wrote", os.path.abspath(OUT))
