#!/usr/bin/env python3
"""Generates docs/img/architecture.svg. Shows what is deployed today.

    python3 docs/diagrams/architecture.py && python3 docs/diagrams/render.py docs/img/architecture.svg docs/img/architecture.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from archlib import Diagram  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "img", "architecture.svg")

W, H = 1400, 1330
d = Diagram(W, H, "MCP gateway on GKE: what is deployed today",
            "agentgateway 1.6 · Keycloak · 3 MCP servers · Workload Identity (keyless) · NetworkPolicy · MCP 2026-07-28")

d.group(190, 96, 1190, 940, "Google Cloud project · europe-west1", "#1a73e8", dash=False, fill="#f8faff", label_w=300)
d.group(215, 130, 770, 730, "GKE Autopilot cluster  mcp-gw-eu", "#5f6368", dash=True, label_w=260)
d.group(240, 170, 200, 670, "ns agentgateway-system", "#8e44ad", dash=True, fill="#faf5fd", label_w=170)
d.group(460, 170, 505, 670, "ns mcp-platform  (Helm chart)", "#00897b", dash=True, fill="#f4fbfa", label_w=210)
d.group(700, 330, 245, 450, "MCP servers · ingress only from the gateway", "#d93025", dash=True, fill="#fffafa", label_w=310)

d.node("cli", 90, 520, "MCP client", "user", "actor", "agent / tests/cli.py\nvia port-forward")
d.node("ctrl", 340, 330, "agentgateway\ncontrol plane", "policy", "compute", "fetches Keycloak's keys,\nprograms the proxy")
d.node("kc", 590, 250, "Keycloak", "key", "security", "IdP · realm mcp\nissues JWTs")
d.node("prx", 590, 520, "agentgateway\nproxy", "lb", "network", "JWT · role · per-tool RBAC\nno public listener")
d.node("k8s", 820, 420, "mcp-k8s", "run", "compute", "read-only pods,\nevents, logs")
d.node("fin", 820, 570, "mcp-finops", "run", "compute", "cost by service / SKU")
d.node("tix", 820, 710, "mcp-tickets", "run", "compute", "list/get · create/update")

d.node("kapi", 1100, 420, "Kubernetes API", "cloud", "compute", "Role: get/list pods,\nlogs, events, one ns")
d.node("bq", 1100, 570, "BigQuery", "db", "data", "dataset mcp_finops\n(dataViewer, read-only)")
d.node("fs", 1100, 710, "Firestore", "db", "data", "database mcp-tickets-*\n(datastore.user, IAM-conditioned)")

d.edge("cli", "prx", "h", label="1 2 4  MCP + discovery", lab_dy=-10)
d.path([(90, 488), (90, 250), (558, 250)], num=3, label="client credentials", lab_at=(300, 242))
d.path([(622, 520), (680, 520), (680, 420), (788, 420)], num=7, color="#d93025")
d.path([(680, 520), (680, 570), (788, 570)], color="#d93025")
d.path([(680, 570), (680, 710), (788, 710)], color="#d93025")
d.badge(652, 470, "5")
d.edge("k8s", "kapi", "h", num=8, color="#00897b", dash=True)
d.edge("fin", "bq", "h", color="#00897b", dash=True)
d.edge("tix", "fs", "h", color="#00897b", dash=True)

d.node("cb", 330, 940, "Cloud Build", "pipeline", "dev", "dedicated SA: 3 permissions")
d.node("ar", 600, 940, "Artifact Registry", "registry", "dev", "image tag = hash of sources")
d.edge("cb", "ar", "h", label="push", color="#5f6368")
d.text(700, 934, "nodes pull as the default compute SA,", 11.5)
d.text(700, 950, "read access on this one repo only.", 11.5)

d.legend(34, 1080, "Flow (every step asserted live by scripts/test.sh, 49 checks)", [
    ("1-4", "No token -> 401 + resource_metadata; client reads the discovery documents; authenticates at Keycloak (3); retries with the JWT"),
    ("5", "Gateway: signature, issuer, audience, expiry; needs a role (403 without); per-tool rule hides tools the role may not use"),
    ("7", "Gateway -> server with its OWN Google ID token (audience = that one server), never the caller's token; the server verifies it"),
    ("8", "Each server reaches its Google service as its own Workload Identity service account: no keys exist anywhere"),
], w=1340)
d.text(34, 1240, "Not built yet (planned): AI agent (ADK + Gemini) · gateway-side rate limits, tracing, TLS · Keycloak on a persistent database · human login (auth code + PKCE) · CI.", 12, "#b3261e", "600")
d.key(34, 1262)

if __name__ == "__main__":
    d.save(OUT)
    print("wrote", os.path.abspath(OUT))
