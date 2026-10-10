# MCP gateway on GKE

A reference implementation of an **MCP gateway**: one controlled entry point between AI agents and the MCP servers they use.
Agents authenticate against an identity provider, receive a short-lived token, and call a single gateway URL. The gateway
verifies the token, decides which tools that caller may see or call, and forwards each call to the right server using its
own identity. Three MCP servers, a Gemini-based agent and the supporting infrastructure run on Google Kubernetes Engine
(Autopilot) with [agentgateway](https://agentgateway.dev) 1.6 and the MCP specification revision 2026-07-28.

![Architecture](docs/img/architecture.png)

## How a request flows

The numbers match the architecture diagram above.

1. **The client calls the gateway without a token.**
2. **The gateway answers `401`.** HTTP 401 *Unauthorized* means "I cannot tell who you are": there was no token, or it was
   invalid or expired. The reply also points to the gateway's discovery documents, which say where to log in. This is how an
   MCP client that has never seen the server finds the identity provider on its own.
3. **The client logs in at Keycloak** (OAuth 2.0 client-credentials grant, with its client id and secret) and receives an
   access token: a signed JWT, valid for 5 minutes, that names the caller's role (`mcp-viewer` or `mcp-operator`) and the
   audience it is meant for (this gateway).
4. **The client repeats the request, now carrying the token** (`Authorization: Bearer <token>`).
5. **The gateway verifies and authorizes.** It checks the token's signature, issuer, audience and expiry (failure: `401`),
   requires a role (no role: `403`, HTTP *Forbidden*, meaning "I know who you are, but you are not allowed"), and applies the
   per-tool rule: a viewer is shown, and may call, only an explicit allow-list of read tools; an operator may use every tool.
   A tool a role may not use is hidden from its tool list and a direct call answers "Unknown tool".
6. **The gateway calls the right server as itself.** It presents a Google-signed ID token whose audience is that one server;
   the caller's token is never forwarded. The server verifies that credential and runs the tool.
7. **The server reaches its data service** (Kubernetes API, BigQuery or Firestore) with its own least-privilege cloud
   identity. No service-account keys exist anywhere in the system.
8. **The AI agent** is a Kubernetes Job that performs steps 1 to 7 inside the cluster, as a viewer or an operator, and lets
   Gemini (Vertex AI) choose which tools to call.

The mechanics behind each step are described in [docs/architecture.md](docs/architecture.md). The concepts (MCP, OAuth,
JWT, one gateway versus many, best practices) are explained from first principles in
[docs/how-mcp-works.md](docs/how-mcp-works.md).

## Components

| Component | What it is | Where it runs |
|---|---|---|
| **agentgateway** | MCP-aware gateway built on the Kubernetes Gateway API. A controller programs a proxy; the proxy terminates MCP, authenticates, authorizes, federates tools and routes calls | `agentgateway-system` (controller), `mcp-platform` (proxy) |
| **Keycloak** | Identity provider. Issues JWTs; the realm (roles, clients, claim mappers, signing key) is defined as code | `mcp-platform` |
| **MCP servers** (`k8s`, `finops`, `tickets`) | Small stateless Python services, one image, selected by the `SERVER` variable | `mcp-platform` |
| **AI agent** | A Python program that gets a token, asks the gateway for its tools, and lets Gemini choose which to call. Runs as a Kubernetes Job | `mcp-platform` |
| **Google Cloud** | GKE Autopilot, BigQuery, Firestore, Vertex AI (Gemini), Artifact Registry, Cloud Build, IAM / Workload Identity | one project, `europe-west1` |

### The MCP servers

| Server | Tools | Backed by | Cloud identity and scope | Callable by |
|---|---|---|---|---|
| `k8s` | `list_pods`, `get_pod_events`, `get_pod_logs` | Kubernetes API | a `Role` that allows only `get`/`list` on pods, pod logs and events in one namespace | viewer, operator |
| `finops` | `cost_by_service`, `top_cost_skus` | BigQuery (synthetic billing table) | `bigquery.dataViewer` on one dataset plus `jobUser`; two fixed, parameterised queries, no free-form SQL | viewer, operator |
| `tickets` | `list_tickets`, `get_ticket`, `create_ticket`, `update_ticket_status` | Firestore | `datastore.user` restricted by an IAM condition to one database | read tools: viewer, operator; write tools: operator only |

Through the gateway a tool is named `<server>_<tool>`, for example `tickets_create_ticket`.

## Security layers

Each layer is enforced independently and tested independently, so a failure of one does not open the system.

| Layer | Mechanism | Rejects with |
|---|---|---|
| Caller identity | JWT validation at the gateway: signature, issuer, audience, expiry | `401` |
| Caller authorization | Role required; per-tool allow-list (default deny); denied tools are hidden from `tools/list` | `403`, or "Unknown tool" |
| Gateway → server | Per-server Google ID token; the server verifies signature, issuer, audience, expiry and that the caller is the gateway's service account | `401` / `403` |
| Network | Default-deny ingress; only the gateway may reach the servers; servers and agent have restricted egress | connection blocked |
| Cloud identity | One Google service account per workload, narrowest role, bound with Workload Identity; no keys | IAM denial |
| Input | Tools validate arguments (enums, length limits, UUIDs, clamped numbers, allow-listed namespace) | tool error |
| Audit | Gateway access log (status, caller) and one structured line per tool call in every server (caller, tool, outcome; argument values are never logged) | n/a |

## The AI agent

`./scripts/ask.sh viewer "<question>"` runs the agent once as a Kubernetes Job and prints a transcript. The same code runs
with two identities, and what it can do is decided by the token it receives, not by its prompt:

| Identity | Tools the gateway offers | Can write |
|---|---|---|
| `agent-viewer` | 7 read tools | no, write tools are not in its tool list |
| `agent-operator` | all 9 tools | yes (`tickets_create_ticket`, `tickets_update_ticket_status`) |

Text returned by a tool (a ticket description, a log line) is untrusted: it can contain instructions aimed at the model
(prompt injection). The agent labels tool output as data and its system prompt forbids following it, but that is a
mitigation, not a guarantee. The guarantee comes from the token: a viewer agent that is fully fooled still has no write
tool to call. The test suite plants an injection payload in a ticket to verify this.

![Agent answering a two-server question](docs/evidence/agent-facts.png)
![Agent reading a ticket that contains an injection payload](docs/evidence/agent-injection.png)
![Agent with an operator token creating a ticket](docs/evidence/agent-write.png)

## Tests

`./scripts/test.sh` runs 70 checks against the live cluster, in four suites. Every "denied" check is paired with a check
that the same action succeeds where it should, so a broken setup cannot pass by rejecting everything.

| Suite | What it verifies | Checks |
|---|---|---|
| Flow | discovery documents; token issuance; gateway authentication (garbage, untrusted-key, tampered and wrong-audience tokens → 401, no role → 403); per-tool authorization; data from all three servers; input validation | 30 |
| Isolation | each server refuses everyone but the gateway (including a genuine Google-signed token for another audience); only the gateway can reach the servers; servers can open HTTPS but not plain HTTP or SMTP | 19 |
| Agent | the Gemini agent answers a question that needs two servers; a prompt-injection payload cannot make a viewer agent write; an operator agent creates a ticket | 12 |
| Resilience | after killing Keycloak the same signing key is used and no gateway change is needed; after replacing the gateway pod the same policy applies | 9 |

The repository was verified from an empty project: everything destroyed with `down.sh`, rebuilt with `up.sh`, and the full
suite passed (last run 2026-10-10).

![Flow tests](docs/evidence/test-output-flow.png)
![Isolation tests](docs/evidence/test-output-isolation.png)
![Agent tests](docs/evidence/test-output-agent.png)
![Resilience tests](docs/evidence/test-output-resilience.png)

## Running it

Prerequisites: `gcloud` (authenticated, project with billing), `terraform` ≥ 1.9, `kubectl`, `helm` ≥ 3.12, `python3`,
`openssl`, `bq`, and the `gke-gcloud-auth-plugin`.

```bash
gcloud config set project <your-project>
./scripts/up.sh      # about 15 minutes: cluster, registry, identities, image builds, data, Keycloak, servers, policies
./scripts/test.sh    # 70 live checks, about 10 minutes; exits non-zero if any fail
./scripts/ask.sh viewer "How many pods are running, and which cloud service costs the most?"
./scripts/pf.sh      # keep port-forwards open, then use tests/cli.py by hand
./scripts/down.sh    # destroy everything and verify nothing billable remains
```

No environment is kept running by this repository. Generated secrets (client secrets, Keycloak admin password, the realm
signing key) are written to `.secrets/`, which is git-ignored.

**Cost.** The GKE free-tier credit covers the management fee of one Autopilot cluster. You pay for the CPU and memory the
pods request (about six small pods, plus a short-lived Job per agent run), BigQuery and Firestore usage (negligible here)
and Gemini tokens. The gateway has no public load balancer. Tear down when finished.

Hands-on commands and troubleshooting: [docs/runbook.md](docs/runbook.md).

## Repository layout

```text
terraform/     cluster, per-workload Google service accounts and Workload Identity bindings, BigQuery, Firestore,
               Artifact Registry, build identity
deploy/chart/  Helm chart: Keycloak with the realm as code, the three servers, Gateway/Backend/Route, policies,
               NetworkPolicies, RBAC, agent resources
servers/       the MCP servers, the shared security kit (mcpkit), unit tests, Dockerfile, cloudbuild.yaml
agent/         the AI agent (Gemini via Vertex AI plus an MCP client), Dockerfile, cloudbuild.yaml
tests/         test_flow, test_isolation, test_agent, test_resilience; cli.py (call the gateway by hand)
scripts/       up, test, ask, pf, down, build-images, seed-finops, capture-evidence, lib
docs/          how-mcp-works, architecture, runbook; diagram and screenshot generators (docs/diagrams);
               captured evidence (docs/evidence)
```

## Documentation

| Document | Read it for |
|---|---|
| [docs/how-mcp-works.md](docs/how-mcp-works.md) | What MCP, a gateway, OAuth and JWT are, how they fit together, one gateway versus many, best practices |
| [docs/architecture.md](docs/architecture.md) | How this system works internally: components, request lifecycle, every check and where it runs, identities, network, deployment, decisions, failure behaviour |
| [docs/runbook.md](docs/runbook.md) | Operating it: build, test, call it by hand, change policy, add a server, troubleshooting |

## Limitations

These are deliberate scope boundaries, not undocumented behaviour.

* **No public entry point.** The gateway is cluster-internal and reached with `kubectl port-forward`. A public deployment
  needs TLS termination, a WAF and an allow-list in front of it.
* **Keycloak runs in development mode** with an in-memory database. The realm is re-imported from a Secret at every start
  and its signing key is pinned, so restarts are harmless, but runtime state (sessions, anything created after start-up) is
  lost and the key sits in a Kubernetes Secret. Production needs a persistent database and an HSM- or KMS-backed key with a
  rotation procedure.
* **Machine clients only.** Agents use the client-credentials grant. Human login (authorization code with PKCE) and dynamic
  client registration (deprecated in the 2026-07-28 MCP revision in favour of Client ID Metadata Documents) are not
  exercised.
* **An operator agent can be manipulated into writing.** Roles bound the damage of a fooled model to what its role allows;
  the operator role still has write tools. Production needs human approval for writes, per-caller rate limits and output
  filtering. None are implemented.
* **The end user's identity stops at the gateway.** Servers know that the gateway called, not which user is behind the call;
  the gateway's access log records the caller. Propagating a user identity downstream needs token exchange.
* **No rate limits or distributed tracing** at the gateway.
* **Images are built in Cloud Build by a dedicated service account but are not scanned or signed**, and there is no CI
  pipeline. Nodes pull as the project's default compute service account, limited to the one repository; a dedicated node
  account requires a new cluster.
* **One replica per workload**, with no PodDisruptionBudgets or autoscaling.
* **The billing data is synthetic**, generated by `scripts/seed-finops.sh`.
* **The cluster API is reachable only from the public IP used at `up.sh` time** (`authorized_cidr`); re-run `up.sh` if it changes.
