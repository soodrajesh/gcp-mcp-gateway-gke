# MCP gateway on GKE

An AI agent should not call every MCP server directly. This repo puts **one gateway** in front of them: the agent
authenticates against an **identity provider (Keycloak)**, gets a **JWT**, and sends it to the gateway; the gateway
verifies it, decides which *tools* that caller may see or call, and forwards to the right MCP server **as itself**
(never with the caller's token). Three real servers sit behind it, and a Gemini-based AI agent uses them. Runs on GKE Autopilot with
[agentgateway](https://agentgateway.dev) 1.6 (Gateway API, MCP spec 2026-07-28).

> **Status: milestone 4 of 4 (core complete).** Identity, gateway policy, three real MCP servers with keyless cloud
> identities, service-to-service authentication, network lockdown, an AI agent and restart resilience are built and tested
> end to end (**70 live checks**). Rate limits, tracing, a TLS entry point and CI are listed under [Roadmap](#roadmap).
> **Verified from zero on 2026-10-10:** everything was destroyed with `./scripts/down.sh`, rebuilt with `./scripts/up.sh`
> (cluster, registry, identities, two image builds, data, Keycloak, servers, policies; about 15 minutes) and `./scripts/test.sh`
> passed 70/70 on the fresh build. Every output below is captured from real runs.

![Architecture](docs/img/architecture.png)

**New to MCP gateways? Start with [docs/how-mcp-works.md](docs/how-mcp-works.md)**
(components, request flow, OAuth/JWT, one vs many gateways, best practices).

## The three servers

| Server | Tools | Backed by | Identity | Who may call (at the gateway) |
|---|---|---|---|---|
| `k8s` | `list_pods`, `get_pod_events`, `get_pod_logs` | Kubernetes API | a `Role` with get/list on pods, pod logs, events in **one namespace** | viewer, operator |
| `finops` | `cost_by_service`, `top_cost_skus` | BigQuery (synthetic billing table) | Google SA with `dataViewer` on **one dataset** + `jobUser`; two fixed parameterised queries, no free SQL | viewer, operator |
| `tickets` | `list_tickets`, `get_ticket`, **`create_ticket`, `update_ticket_status`** | Firestore | Google SA with `datastore.user` **conditioned on one database** | read: viewer, operator; **write: operator only** |

## What works today (`./scripts/test.sh`: 70 checks in four suites)

| Suite | Claim | Checks |
|---|---|---|
| **Flow** | Discovery (401 + `resource_metadata`, metadata documents) · Keycloak tokens · gateway authentication (garbage / untrusted-key / tampered / wrong-audience → 401, no role → 403) · per-tool authorization with a default-deny viewer allow-list (7 of 9 tools) · real data from all three servers · bad input and a disallowed namespace refused | 30 |
| **Isolation** | Each server refuses anyone but the gateway (tested directly, including a *genuine Google-signed token for another audience*) · only the gateway reaches the servers (NetworkPolicy, each block paired with a positive control) · servers can open HTTPS but not HTTP or SMTP | 19 |
| **Agent** | A Gemini agent runs as a Kubernetes Job and answers a question needing two servers · a **prompt-injection payload** in a ticket cannot make a viewer agent write: it was never offered a write tool · the same agent code as an operator really creates a ticket in Firestore | 12 |
| **Resilience** | Kill Keycloak: the realm comes back, **the same signing key id** signs, the gateway needs no change, and tokens issued before the restart still work · replace the gateway pod: same policy afterwards | 9 |

![Flow tests](docs/evidence/test-output-flow.png)
![Isolation tests](docs/evidence/test-output-isolation.png)
![Agent tests](docs/evidence/test-output-agent.png)
![Resilience tests](docs/evidence/test-output-resilience.png)

## The AI agent

`./scripts/ask.sh viewer "<question>"` runs the agent once as a Kubernetes Job and prints its transcript. The **same
code** runs with two identities; what it can do is decided by its token, not its code:

| Identity | Tools the gateway offers | Can it write? |
|---|---|---|
| `agent-viewer` | 7 read tools | no: write tools are not even in its tool list |
| `agent-operator` | 9 tools | yes: `tickets_create_ticket`, `tickets_update_ticket_status` |

![Agent: two servers](docs/evidence/agent-facts.png)
![Agent: prompt injection](docs/evidence/agent-injection.png)
![Agent: operator writes](docs/evidence/agent-write.png)

In the injection run the model recognised the planted instruction and ignored it. **Do not rely on that.** The guarantee is
structural: the viewer's token carries no write tools, so even a fully fooled model has nothing to call. (An operator agent
*is* exposed to injection through its write tools; production needs an approval step for writes. See Known gaps.)

By hand, the servers' own audit lines, the gateway log, what runs, and the identities:

![By hand](docs/evidence/cli-session.png)
![Server audit](docs/evidence/server-audit.png)
![Gateway log](docs/evidence/gateway-log.png)
![Cluster](docs/evidence/cluster.png)
![Identities](docs/evidence/iam.png)

## Run it

```bash
gcloud config set project <your-project>     # billing linked
./scripts/up.sh      # ~15 min from nothing: cluster, registry, identities, image builds, data, Keycloak, servers, agent, policies
./scripts/test.sh    # 70 live checks (~10 min); non-zero exit if any fail
./scripts/ask.sh viewer "How many pods are running, and which cloud service costs the most?"
./scripts/pf.sh      # (optional) keep port-forwards open, then use tests/cli.py by hand
./scripts/down.sh    # delete everything and prove nothing billable is left
```
Hands-on commands and troubleshooting: [docs/runbook.md](docs/runbook.md). Design notes: [docs/architecture.md](docs/architecture.md).

**Cost:** the GKE free-tier credit covers the management fee for one Autopilot cluster; you pay for requested pod
CPU/memory (about 6 small pods, plus a short-lived Job per agent run and Gemini tokens) and nothing else of note (no public load balancer any more). Leave it up for hours, not weeks.

## Layout

```
terraform/     cluster, per-workload Google service accounts + Workload Identity, BigQuery, Firestore, registry, build identity
deploy/chart/  Helm chart: Keycloak + realm-as-code, 3 servers, Gateway/Backend/Route, policies, NetworkPolicies, RBAC
servers/       the MCP servers (Python) + the shared security kit (mcpkit) + unit tests + Dockerfile + cloudbuild.yaml
agent/         the AI agent (Gemini via Vertex AI + the MCP client), Dockerfile, cloudbuild.yaml
tests/         test_flow · test_isolation · test_agent · test_resilience, cli.py (poke it by hand), transcript.py
scripts/       up · test · ask · pf · down · build-images · seed-finops · capture-evidence · lib
docs/          how-mcp-works, architecture, runbook, diagram generator, captured evidence
```

## Roadmap

| Milestone | Status |
|---|---|
| 1. Gateway on Autopilot, federation, JWT, per-tool RBAC | done |
| 2. Keycloak IdP + MCP discovery flow + role check | done |
| 3. Three real servers, Workload Identity per server, gateway-to-server auth, NetworkPolicy, image build, audit lines | done |
| 4. **AI agent (Gemini), prompt-injection test, pinned IdP signing key, resilience tests** | **done** |
| Next | gateway rate limits and OpenTelemetry tracing · TLS public entry point (WAF, allow-list) · Keycloak on a persistent database · human login (auth code + PKCE) · image scanning/signing · CI · an approval step for agent writes |

## Known gaps (deliberate for now)

* **The gateway is cluster-internal only.** Reached through `kubectl port-forward`. A public, TLS-terminated entry point (with a WAF and an allow-list) is milestone 4.
* **Keycloak runs in dev mode with an in-memory database.** The realm is re-imported from a Secret on every start, and its signing key is pinned (generated once into `.secrets/`), so restarts are harmless (tested). But sessions and anything created at runtime are lost, and the key sits in a Kubernetes Secret: production wants a persistent database, an HSM/KMS-backed key and a rotation procedure (rotation = add the new key, wait out the gateway's 5-minute key cache, retire the old).
* **Machine clients only.** Client-credentials grant. Human login (authorization code + PKCE) and dynamic client registration (deprecated in the July 2026 MCP revision in favour of Client ID Metadata Documents) are not exercised.
* **An operator agent can be talked into writing.** The viewer/operator split limits the damage of a fooled model to what its role allows; an operator agent still has write tools. Production needs human approval for writes, rate limits per caller, and output filtering. None are built.
* **End-user identity stops at the gateway.** Servers know "the gateway called", not which user; the gateway's access log has the caller. Propagating a user identity downstream (token exchange) is not done.
* **Images are built by Cloud Build as a dedicated service account, but nothing scans or signs them yet** (no vulnerability gate, no Binary Authorization). Nodes pull as the default compute service account, limited to this one repository; a dedicated node account needs a new cluster.
* **One replica per workload**, no PodDisruptionBudgets or autoscaling. Fine for a lab, not for an SLO.
* The synthetic billing data is generated by `scripts/seed-finops.sh`; it is not real billing.
* The cluster API is reachable from your public IP only (`authorized_cidr`); if your IP changes, re-run `up.sh`.
