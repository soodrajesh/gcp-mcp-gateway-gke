# MCP gateway on GKE

An AI agent should not call every MCP server directly. This repo puts **one gateway** in front of them: the agent
authenticates against an **identity provider (Keycloak)**, gets a **JWT**, and sends it to the gateway; the gateway
verifies it, decides which *tools* that caller may see or call, and forwards to the right MCP server **as itself**
(never with the caller's token). Three real servers sit behind it. Runs on GKE Autopilot with
[agentgateway](https://agentgateway.dev) 1.6 (Gateway API, MCP spec 2026-07-28).

> **Status: milestone 3 of 4.** Identity, gateway policy, three real MCP servers with keyless cloud identities,
> service-to-service authentication and network lockdown are built and tested end to end (49 live checks). The AI agent
> and the observability/TLS hardening are next. See [Roadmap](#roadmap). Every output below is captured from a real run.

![Architecture](docs/img/architecture.png)

**New to MCP gateways? Start with [docs/how-mcp-works.md](docs/how-mcp-works.md)**
(components, request flow, OAuth/JWT, one vs many gateways, best practices).

## The three servers

| Server | Tools | Backed by | Identity | Who may call (at the gateway) |
|---|---|---|---|---|
| `k8s` | `list_pods`, `get_pod_events`, `get_pod_logs` | Kubernetes API | a `Role` with get/list on pods, pod logs, events in **one namespace** | viewer, operator |
| `finops` | `cost_by_service`, `top_cost_skus` | BigQuery (synthetic billing table) | Google SA with `dataViewer` on **one dataset** + `jobUser`; two fixed parameterised queries, no free SQL | viewer, operator |
| `tickets` | `list_tickets`, `get_ticket`, **`create_ticket`, `update_ticket_status`** | Firestore | Google SA with `datastore.user` **conditioned on one database** | read: viewer, operator; **write: operator only** |

## What works today (`./scripts/test.sh`: 30 flow checks + 19 isolation checks)

| Claim | Checks |
|---|---|
| **Discovery**: no token → 401 + `WWW-Authenticate: resource_metadata=<url>`; the gateway serves protected-resource and authorization-server metadata | 5 |
| **Token issuance**: Keycloak issues a JWT (client-credentials) with the right issuer, audience = this gateway and a role; a wrong secret gets no token | 5 |
| **Gateway authentication**: garbage, untrusted-key, tampered, and not-addressed-to-this-gateway tokens → 401; valid but no role → 403; the real token is accepted | 6 |
| **Per-tool authorization (default deny)**: a viewer sees exactly 7 read tools, an operator all 9; write calls by a viewer are refused | 5 |
| **Real servers, real data**: pods from the Kubernetes API, costs from BigQuery, a ticket created, read back, closed and listed in Firestore; bad input and a disallowed namespace are refused by the servers | 9 |
| **Each server refuses anyone but the gateway** (tested directly, bypassing the gateway): no token, garbage, and a *genuine Google-signed token for a different audience* → 401 | 10 |
| **Network lockdown** (NetworkPolicy): only the gateway reaches the servers; even a labelled client cannot; each block has a positive control | 6 |
| **Limited egress**: a server can open HTTPS but not plain HTTP or SMTP | 3 |

![Flow tests](docs/evidence/test-output-flow.png)
![Isolation tests](docs/evidence/test-output-isolation.png)

By hand, the servers' own audit lines, the gateway log, what runs, and the identities:

![By hand](docs/evidence/cli-session.png)
![Server audit](docs/evidence/server-audit.png)
![Gateway log](docs/evidence/gateway-log.png)
![Cluster](docs/evidence/cluster.png)
![Identities](docs/evidence/iam.png)

## Run it

```bash
gcloud config set project <your-project>     # billing linked
./scripts/up.sh      # ~15 min from nothing: cluster, registry, identities, image build, data, Keycloak, servers, policies
./scripts/test.sh    # 49 live checks; non-zero exit if any fail
./scripts/pf.sh      # (optional) keep port-forwards open, then use tests/cli.py by hand
./scripts/down.sh    # delete everything and prove nothing billable is left
```
Hands-on commands and troubleshooting: [docs/runbook.md](docs/runbook.md). Design notes: [docs/architecture.md](docs/architecture.md).

**Cost:** the GKE free-tier credit covers the management fee for one Autopilot cluster; you pay for requested pod
CPU/memory (about 6 small pods) and nothing else of note (no public load balancer any more). Leave it up for hours, not weeks.

## Layout

```
terraform/     cluster, per-workload Google service accounts + Workload Identity, BigQuery, Firestore, registry, build identity
deploy/chart/  Helm chart: Keycloak + realm-as-code, 3 servers, Gateway/Backend/Route, policies, NetworkPolicies, RBAC
servers/       the MCP servers (Python) + the shared security kit (mcpkit) + unit tests + Dockerfile + cloudbuild.yaml
tests/         test_flow.py, test_isolation.py, cli.py (poke it by hand)
scripts/       up · test · pf · down · build-images · seed-finops · capture-evidence · lib
docs/          how-mcp-works, architecture, runbook, diagram generator, captured evidence
```

## Roadmap

| Milestone | Status |
|---|---|
| 1. Gateway on Autopilot, federation, JWT, per-tool RBAC | done |
| 2. Keycloak IdP + MCP discovery flow + role check | done |
| 3. **Three real servers, Workload Identity per server, gateway-to-server auth, NetworkPolicy, image build, audit lines** | **done** |
| 4. AI agent (ADK + Gemini on Vertex) running the full flow; gateway rate limits, tracing, TLS; CI | next |

## Known gaps (deliberate for now)

* **The gateway is cluster-internal only.** Reached through `kubectl port-forward`. A public, TLS-terminated entry point (with a WAF and an allow-list) is milestone 4.
* **Keycloak runs in dev mode with an in-memory database.** A restart loses the realm and rotates signing keys; the gateway caches the keys (minimum 5 min), so valid tokens get 401 `unknown key` until the policy is re-applied (see the runbook). Production: persistent database and a rotation policy.
* **Machine clients only.** Client-credentials grant. Human login (authorization code + PKCE) and dynamic client registration (deprecated in the July 2026 MCP revision in favour of Client ID Metadata Documents) are not exercised.
* **End-user identity stops at the gateway.** Servers know "the gateway called", not which user; the gateway's access log has the caller. Propagating a user identity downstream (token exchange) is not done.
* **Images are built by Cloud Build as a dedicated service account, but nothing scans or signs them yet** (no vulnerability gate, no Binary Authorization). Nodes pull as the default compute service account, limited to this one repository; a dedicated node account needs a new cluster.
* **One replica per workload**, no PodDisruptionBudgets or autoscaling. Fine for a lab, not for an SLO.
* The synthetic billing data is generated by `scripts/seed-finops.sh`; it is not real billing.
* **A from-zero rebuild (cluster included) of the milestone-3 Terraform has not been run end to end**; the infrastructure was applied incrementally and the platform layer was rebuilt from an empty namespace.
* The cluster API is reachable from your public IP only (`authorized_cidr`); if your IP changes, re-run `up.sh`.
