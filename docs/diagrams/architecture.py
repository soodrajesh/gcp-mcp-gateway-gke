#!/usr/bin/env python3
"""Generates docs/img/architecture.svg, the system architecture diagram.

    python3 docs/diagrams/architecture.py && python3 docs/diagrams/render.py docs/img/architecture.svg docs/img/architecture.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from archlib import Diagram  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "img", "architecture.svg")

W, H = 1400, 1570
d = Diagram(W, H, "MCP gateway on GKE: architecture",
            "agentgateway 1.6 · Keycloak · 3 MCP servers · Gemini agent · Workload Identity (keyless) · NetworkPolicy · MCP 2026-07-28")

d.group(190, 96, 1190, 1010, "Google Cloud project · europe-west1", "#1a73e8", dash=False, fill="#f8faff", label_w=300)
d.group(215, 130, 770, 800, "GKE Autopilot cluster  mcp-gw-eu", "#5f6368", dash=True, label_w=260)
d.group(240, 170, 200, 740, "ns agentgateway-system", "#8e44ad", dash=True, fill="#faf5fd", label_w=170)
d.group(460, 170, 505, 740, "ns mcp-platform  (Helm chart)", "#00897b", dash=True, fill="#f4fbfa", label_w=210)
d.group(700, 330, 245, 450, "MCP servers · ingress only from the gateway", "#d93025", dash=True, fill="#fffafa", label_w=310)

d.node("cli", 90, 520, "Operator laptop", "user", "actor", "tests/cli.py · scripts/ask.sh\nvia port-forward")
d.node("ctrl", 340, 330, "agentgateway\ncontrol plane", "policy", "compute", "fetches Keycloak's keys,\nprograms the proxy")
d.node("kc", 590, 250, "Keycloak", "key", "security", "IdP · realm mcp\nissues JWTs")
d.node("prx", 590, 520, "agentgateway\nproxy", "lb", "network", "JWT · roles · RBAC")
d.node("k8s", 820, 420, "mcp-k8s", "run", "compute", "read-only pods,\nevents, logs")
d.node("fin", 820, 570, "mcp-finops", "run", "compute", "cost by service / SKU")
d.node("tix", 820, 710, "mcp-tickets", "run", "compute", "list/get · create/update")

d.node("agt", 660, 760, "AI agent", "sparkle", "ai", "a Job: same code,\nviewer or operator token")
d.node("kapi", 1100, 420, "Kubernetes API", "cloud", "compute", "Role: get/list pods,\nlogs, events, one ns")
d.node("bq", 1100, 570, "BigQuery", "db", "data", "dataset mcp_finops\n(dataViewer, read-only)")
d.node("fs", 1100, 710, "Firestore", "db", "data", "database mcp-tickets-*\n(datastore.user, IAM-conditioned)")

# steps 1, 2 and 4 are three separate arrows between the client and the gateway
d.path([(122, 494), (558, 494)])
d.path([(558, 520), (122, 520)])
d.path([(122, 546), (558, 546)])
for y, n, txt in ((494, "1", "request, no token"), (520, "2", "reply 401 + where to log in"), (546, "4", "same request + token")):
    d.badge(500, y, n)
    d.text(140, y - 5, txt, 11.5, "#202124", "600")
d.path([(90, 488), (90, 250), (558, 250)], num=3, label="3  log in (client id + secret), get a token", lab_at=(300, 242))
d.badge(520, 468, "5")
d.text(536, 472, "verify + authorize", 11.5, "#d93025", "600")
d.path([(622, 520), (680, 520), (680, 420), (788, 420)], num=6, color="#d93025")
d.path([(680, 520), (680, 570), (788, 570)], color="#d93025")
d.path([(680, 570), (680, 710), (788, 710)], color="#d93025")
d.edge("k8s", "kapi", "h", num=7, color="#00897b", dash=True)
d.node("vtx", 1100, 850, "Vertex AI", "sparkle", "ai", "gemini-2.5-flash\n(aiplatform.user only)")
d.path([(660, 728), (660, 565), (624, 565)], num=8)
d.path([(692, 760), (720, 760), (720, 850), (1068, 850)], color="#f29900", dash=True)
d.edge("fin", "bq", "h", color="#00897b", dash=True)
d.edge("tix", "fs", "h", color="#00897b", dash=True)

d.node("cb", 330, 1010, "Cloud Build", "pipeline", "dev", "dedicated SA: 3 permissions")
d.node("ar", 600, 1010, "Artifact Registry", "registry", "dev", "image tag = hash of sources")
d.edge("cb", "ar", "h", label="push", color="#5f6368")
d.text(700, 1004, "nodes pull as the default compute SA,", 11.5)
d.text(700, 1020, "read access on this one repo only.", 11.5)

d.text(34, 1140, "HTTP status codes used:  401 Unauthorized = the gateway cannot tell who is calling (no token, or an invalid or expired one).", 12.5, "#202124", "600")
d.text(34, 1159, "403 Forbidden = it knows who is calling, but that role is not allowed.  A tool the role may not use is hidden and answers \"Unknown tool\".", 12.5, "#202124", "600")
d.legend(34, 1185, "Request flow (every step is asserted by scripts/test.sh)", [
    ("1", "The client calls the gateway without a token."),
    ("2", "The gateway answers 401 and points to its discovery documents, which say where to log in (the identity provider)."),
    ("3", "The client logs in at Keycloak with its client id and secret and receives a token (a signed JWT) valid for 5 minutes."),
    ("4", "The client repeats the request, now carrying the token."),
    ("5", "The gateway checks the token (signature, issuer, audience, expiry), requires a role, then applies the per-tool rule."),
    ("6", "For an allowed call, the gateway calls the right server with its OWN Google-signed token for that server, never the caller's."),
    ("7", "Each server reaches its data service (Kubernetes API, BigQuery, Firestore) as its own cloud identity; no keys exist anywhere."),
    ("8", "The AI agent is a Job that does steps 1-7 inside the cluster, as a viewer or an operator; Gemini (Vertex AI) picks the tools."),
], w=1340)
d.text(34, 1465, "Out of scope: gateway rate limits and tracing · TLS public entry point · Keycloak on a persistent database · human login (auth code + PKCE) · image scanning/signing and CI.", 12, "#b3261e", "600")
d.key(34, 1487)

if __name__ == "__main__":
    d.save(OUT)
    print("wrote", os.path.abspath(OUT))
