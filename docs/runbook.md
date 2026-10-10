# Runbook

How to build, test, use, inspect, change and troubleshoot the system. For how it works internally see
[architecture.md](architecture.md).

Contents: [Prerequisites](#prerequisites) · [Lifecycle](#lifecycle) · [Testing](#testing) ·
[Using it by hand](#using-it-by-hand) · [Running the agent](#running-the-agent) · [Inspecting the cluster](#inspecting-the-cluster) ·
[Reading the logs](#reading-the-logs) · [Changing things](#changing-things) · [Secrets and keys](#secrets-and-keys) ·
[Troubleshooting](#troubleshooting) · [Cost and cleanup](#cost-and-cleanup)

## Prerequisites

`gcloud` (authenticated, project with billing enabled), `terraform` ≥ 1.9, `kubectl`, `helm` ≥ 3.12, `python3`, `openssl`, `bq`,
and the GKE auth plugin (`gcloud components install gke-gcloud-auth-plugin`). The scripts add the plugin to `PATH` themselves.

## Lifecycle

```bash
gcloud config set project <your-project>
./scripts/up.sh --plan   # show the Terraform plan, change nothing
./scripts/up.sh          # build everything (about 15 minutes from nothing); safe to re-run
./scripts/down.sh        # destroy everything and print clusters / forwarding rules / addresses (expect 0 0 0)
```

`up.sh` runs six steps: Python virtualenv, Terraform (state bucket, cluster, identities, data stores), cluster credentials,
the agentgateway controller, then the platform (image builds, BigQuery seed, secrets, Helm install with policies disabled,
wait for Keycloak, servers and gateway), and finally the policies. It is idempotent; re-running reconciles. Details of why
the order matters are in [architecture.md §11](architecture.md#11-build-and-deploy).

State kept on your machine and git-ignored: `.secrets/` (generated credentials and the Keycloak signing key),
`.deploy-suffix` (names the Firestore database for this deployment), `.venv/`.

## Testing

```bash
./scripts/test.sh
```

Runs 70 checks in four suites (about 10 minutes) against the live cluster and exits non-zero if any fail:

| Suite | File | Checks | Covers |
|---|---|---|---|
| Flow | `tests/test_flow.py` | 30 | discovery, tokens, gateway authentication, per-tool authorization, the three servers |
| Isolation | `tests/test_isolation.py` | 19 | servers reject non-gateway callers, network policy, egress limits |
| Agent | `tests/test_agent.py` | 12 | three real agent runs as Kubernetes Jobs, including a prompt-injection payload |
| Resilience | `tests/test_resilience.py` | 9 | kills the Keycloak and gateway pods and checks nothing needs re-configuring |

`test.sh` starts the port-forwards it needs. The resilience suite deliberately restarts pods, so run it when the cluster is
otherwise idle. Unit tests for the server security kit run without a cluster:

```bash
.venv/bin/python -m pytest servers/tests -q
```

## Using it by hand

`pf.sh` forwards the gateway to `localhost:18080` and Keycloak to `localhost:18081`. Leave it running in one terminal.

```bash
./scripts/pf.sh
```

In another terminal, `tests/cli.py` obtains a token for the named Keycloak client and calls the gateway:

```bash
.venv/bin/python tests/cli.py token agent-viewer        # decoded token claims: iss, aud, roles
.venv/bin/python tests/cli.py list agent-viewer         # the 7 tools a viewer may use
.venv/bin/python tests/cli.py list agent-operator       # all 9 tools
.venv/bin/python tests/cli.py call agent-viewer k8s_list_pods namespace=mcp-platform
.venv/bin/python tests/cli.py call agent-viewer k8s_list_pods namespace=kube-system   # refused by the server
.venv/bin/python tests/cli.py call agent-viewer finops_cost_by_service days=30
.venv/bin/python tests/cli.py call agent-viewer finops_top_cost_skus days=7 limit=3
.venv/bin/python tests/cli.py call agent-viewer tickets_list_tickets status=open
.venv/bin/python tests/cli.py call agent-viewer tickets_create_ticket title=x         # "Unknown tool": not a viewer tool
.venv/bin/python tests/cli.py call agent-operator tickets_create_ticket title="Fix it" priority=high
.venv/bin/python tests/cli.py list agent-noaud          # 401: a valid Keycloak token, but not addressed to this gateway
.venv/bin/python tests/cli.py list agent-norole         # 403: right audience, no role
curl -s localhost:18080/.well-known/oauth-protected-resource/mcp        # the discovery document
```

Tool names are `<server>_<tool>`. The four clients (`agent-viewer`, `agent-operator`, `agent-norole`, `agent-noaud`) and
their secrets live in the realm (`deploy/chart/files/realm.json.tpl`) and `.secrets/`. A plain `curl` JSON-RPC call to `/mcp`
returns an empty `400` because the 2026-07-28 request format differs from earlier revisions; use `tests/cli.py` or an MCP SDK
client.

## Running the agent

```bash
./scripts/ask.sh viewer   "How many pods are running, and which cloud service costs the most?"
./scripts/ask.sh operator "Open a ticket titled 'Investigate Keycloak restarts' with high priority"
```

`ask.sh` creates a Kubernetes Job from the agent image, waits for it, and prints a readable transcript: the token step, the
tools the gateway offered to that identity, each tool call and result, and the final answer. The same code runs for both
roles; a viewer is offered 7 tools and cannot create tickets. Raw JSON events are available with
`kubectl -n mcp-platform logs job/<name>` until the Job is garbage-collected (15 minutes).

## Inspecting the cluster

```bash
gcloud container clusters get-credentials mcp-gw-eu --region europe-west1
kubectl get pods,svc,networkpolicy -n mcp-platform
kubectl get pods -n agentgateway-system
kubectl -n mcp-platform get gateway,httproute,agentgatewaybackend,agentgatewaypolicy   # Accepted/Attached must be True
kubectl -n mcp-platform get agentgatewaypolicy tool-rbac -o yaml                       # the per-tool rules
```

## Reading the logs

| Log | Command | What a line means |
|---|---|---|
| Gateway access log | `kubectl -n mcp-platform logs deploy/mcp-gateway` | One line per request with `http.status`, `jwt.sub` (the caller) and, on failure, an `error=` reason. `401` = authentication failed, `403` = no role, `400` with "Unknown tool" = tool not allowed for the role |
| Server audit | `kubectl -n mcp-platform logs deploy/mcp-tickets \| grep tool_call` | JSON per tool call: `caller` (the gateway's service account), `tool`, `arg_names` (never values), `outcome`, `ms`. `request_rejected` lines show a refused credential and the reason |
| Controller | `kubectl -n agentgateway-system logs deploy/agentgateway \| grep jwks` | The controller fetching Keycloak's signing keys |
| Keycloak | `kubectl -n mcp-platform logs deploy/keycloak` | Start-up, realm import, token endpoint errors |

## Changing things

| Change | How |
|---|---|
| Which tools a viewer may use | Edit `viewerTools` in `deploy/chart/values.yaml` (per server, the server's own tool names), then `./scripts/up.sh`. Anything unlisted is operator-only; a brand-new tool is hidden from viewers until listed |
| Roles, clients, claim mappers | Edit `deploy/chart/files/realm.json.tpl` and `values.yaml`, then `./scripts/up.sh`. The chart restarts Keycloak on a realm change (checksum annotation) |
| Add a tool to a server | Add a function with `@mcp.tool()` and `@audited("<server>")` in `servers/app/servers/<server>.py`, add unit/live tests, run `./scripts/up.sh` (it rebuilds the image because the source hash changed) |
| Add a server | Add it under `servers:` in `values.yaml`; create `servers/app/servers/<name>.py`; if it needs a Google API, add a service account, Workload Identity binding and the narrowest IAM grant in `terraform/identities.tf` and pass its email through `values.yaml`. The Deployment, Service, ServiceAccount, gateway target, audience (`mcp-<name>`) and network-policy membership come from the chart |
| Change the agent's model or region | `agent.model` / `agent.location` in `values.yaml` |

## Secrets and keys

All generated by `up.sh` into `.secrets/` (git-ignored, mode 600): the Keycloak admin password, the four client secrets, and
the realm signing key pair (`realm-key.pem`, `realm-cert.pem`).

* **Do not delete `.secrets/` from a running deployment.** A new signing key makes the gateway's cached key set stale; valid
  tokens return `401` with `token uses the unknown key` until the policy is recreated (see Troubleshooting).
* **Rotating a client secret:** delete the file under `.secrets/`, run `./scripts/up.sh`; Keycloak restarts with the new secret.
* **Rotating the signing key:** the safe procedure is to add the new key to the realm, wait out the gateway's five-minute key
  cache, then retire the old one. The chart currently pins a single key, so a rotation here means a short window of `401`s.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `kubectl`: `gke-gcloud-auth-plugin not found` | The plugin is in the gcloud SDK bin directory. The scripts handle it; in your shell: `export PATH="$(gcloud info --format='value(installation.sdk_root)')/bin:$PATH"` |
| `kubectl` times out after your IP changed | The cluster API allows only the IP used at `up.sh` time. Re-run `./scripts/up.sh` |
| Valid tokens return `401`, gateway log says `token uses the unknown key` | The signing key changed (usually `.secrets/` was deleted) and the controller still caches the old key set. Delete and recreate the policy: `kubectl -n mcp-platform delete agentgatewaypolicy mcp-authn && ./scripts/up.sh` |
| A realm change had no effect | Keycloak reads its realm only at start-up. The chart rolls the pod on a change; after a manual Secret edit run `kubectl -n mcp-platform rollout restart deploy/keycloak` |
| A policy edit had no effect | `kubectl -n mcp-platform get agentgatewaypolicy`: both `Accepted` and `Attached` must be `True` |
| Viewer sees no tools after a rule change | In a rule, `mcp.tool.name` is the server's own tool name (`list_pods`), not the prefixed name clients see (`k8s_list_pods`). Match `mcp.tool.target` as well |
| Gateway `500`: `idToken auth requires a hostname target or explicit audience` | A selector target was used with ID-token auth. Use a static host target with an explicit audience (as the chart does) |
| Discovery endpoint `500` after tightening network policy | The gateway proxy fetches Keycloak's metadata at request time. Allow proxy → Keycloak (8080) and Keycloak ingress from the proxy |
| `ImagePullBackOff` on server pods | Nodes pull as the default compute service account; it needs `artifactregistry.reader` on the repository (Terraform `nodes_pull`) |
| `up.sh` fails creating Firestore | A deleted database name cannot be reused immediately. `up.sh` uses a fresh `.deploy-suffix` for each deployment; `down.sh` removes it |
| `ask.sh`: "agent produced no events" | The Job failed before the agent started. `kubectl -n mcp-platform describe job <name>`: image pull, Workload Identity, or the `agent-credentials` Secret |
| Agent reports "refused by the gateway" | Expected when a viewer asks for a write. `tests/cli.py list agent-viewer` shows what it may use |
| A denied tool returns "Unknown tool", not `403` | By design: the gateway hides tools the caller may not use. In its log this is `http.status=400`. No or bad token is `401`; a valid token with no role is `403` |
| Isolation test: positive control "labelled client CAN reach the gateway" fails once | A freshly restarted gateway pod can be slow for the first request. The probe retries; a persistent failure is a real network-policy problem |
| Finished agent Job pods linger | Jobs are garbage-collected after 15 minutes; `kubectl -n mcp-platform delete job --all` removes them now |

## Cost and cleanup

Running cost: the GKE Autopilot pods (about six small ones), a short-lived Job per agent run, negligible BigQuery and
Firestore usage, and Gemini tokens. The GKE free-tier credit covers the cluster management fee for one cluster. Destroy
everything when finished:

```bash
./scripts/down.sh
```

It prints the counts of remaining clusters, forwarding rules and reserved addresses; all should be 0. The Terraform state
bucket (`<project>-tfstate`) is deliberately kept.
