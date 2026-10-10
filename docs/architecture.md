# Architecture and internals

This document explains how the system works behind the scenes: which process does what, how configuration reaches it,
what happens to a request at each hop, where every security check runs, and how the pieces are built and deployed.
For the underlying concepts (MCP, OAuth, JWT) read [how-mcp-works.md](how-mcp-works.md) first if they are unfamiliar.

Contents: [1 Overview](#1-overview) · [2 Control plane and data plane](#2-control-plane-and-data-plane) ·
[3 Kubernetes objects](#3-kubernetes-objects) · [4 Request lifecycle](#4-request-lifecycle) ·
[5 How each check works](#5-how-each-check-works) · [6 Identities](#6-identities-and-permissions) ·
[7 Keycloak](#7-keycloak) · [8 Network](#8-network-policy) · [9 The servers](#9-the-mcp-servers) ·
[10 The agent](#10-the-agent) · [11 Build and deploy](#11-build-and-deploy) · [12 Design decisions](#12-design-decisions) ·
[13 Failure behaviour](#13-failure-behaviour-and-operational-notes)

## 1. Overview

![Architecture](img/architecture.png)

Everything application-level runs in one Kubernetes namespace, `mcp-platform`, on a GKE Autopilot cluster. The gateway
controller lives in `agentgateway-system`. Three different trust relationships exist and are kept separate:

| Relationship | Credential | Issued by | Checked by |
|---|---|---|---|
| Caller → gateway | OAuth access token (JWT) | Keycloak | the gateway |
| Gateway → server | Google-signed ID token, one audience per server | Google, on behalf of the gateway's service account | each server |
| Server → cloud service | the server's own Google service account (Workload Identity) | Google IAM | BigQuery / Firestore / the Kubernetes API |

A token is never reused across these boundaries. That separation is what stops a stolen caller token from being replayed
against a server, and a compromised server from reaching anything beyond its own data store.

## 2. Control plane and data plane

agentgateway splits into a **controller** and a **proxy**.

* The **controller** (deployment `agentgateway` in `agentgateway-system`) watches the cluster for Gateway API objects
  (`Gateway`, `HTTPRoute`) and agentgateway's own custom resources (`AgentgatewayBackend`, `AgentgatewayPolicy`,
  `AgentgatewayParameters`). For every `Gateway` it creates a proxy Deployment, a Service and a ServiceAccount in the
  Gateway's namespace, named after the Gateway (here `mcp-gateway`). It translates all the objects into proxy
  configuration and streams it to each proxy over **xDS** (gRPC, port 9978).
* The controller also **fetches Keycloak's public signing keys** (the JWKS) for any policy that references a remote
  key set, and pushes the key material to the proxy as part of that configuration. Fetched keys are cached for the
  configured `cacheDuration` (minimum five minutes).
* The **proxy** (pod `mcp-gateway-…`) is the data plane. It listens on port 80, terminates the MCP protocol, performs
  authentication and authorization, merges tool lists, routes calls and attaches the outgoing credential.

Because the proxy receives the policy from the controller, a policy change takes effect without restarting the proxy. The
proxy also makes one kind of call itself: to serve authorization-server metadata it requests Keycloak's OpenID
configuration at request time, which is why the network policy allows proxy → Keycloak traffic.

## 3. Kubernetes objects

The Helm chart in `deploy/chart` creates everything in `mcp-platform`. The files in `templates/` map to concerns:

| Object | File | Purpose |
|---|---|---|
| `Gateway mcp-gateway` | `gateway.yaml` | Listener on port 80 using the `agentgateway` GatewayClass; references the parameters object below |
| `AgentgatewayParameters mcp-gateway-params` | `gateway.yaml` | Shapes the generated proxy: annotates its ServiceAccount with the Google service account (Workload Identity) and makes its Service `ClusterIP` (no public address) |
| `AgentgatewayBackend mcp` | `gateway.yaml` | One MCP backend with one **static target per server**; each target carries its own outgoing credential |
| `HTTPRoute mcp` | `gateway.yaml` | Routes `/mcp` and the two `/.well-known/…` discovery paths to the backend |
| `AgentgatewayPolicy mcp-authn` | `policies.yaml` | Attached to the route: JWT validation, MCP discovery metadata, and the role requirement |
| `AgentgatewayPolicy tool-rbac` | `policies.yaml` | Attached to the backend: which tools each role may list and call |
| Deployment, Service, ServiceAccount per server | `servers.yaml` | `mcp-k8s`, `mcp-finops`, `mcp-tickets` (same image, `SERVER` env var selects behaviour) |
| `Role` / `RoleBinding mcp-k8s-readonly` | `servers.yaml` | The only Kubernetes permissions any workload here holds (read pods, pod logs, events in `mcp-platform`) |
| Deployment, Service, two Secrets | `keycloak.yaml` | Keycloak, its admin password, and the realm definition mounted for import |
| 7 `NetworkPolicy` objects | `netpol.yaml`, `agent.yaml` | Default-deny ingress plus explicit allows and egress limits |
| ServiceAccount, Secret, ConfigMap | `agent.yaml` | What the agent Jobs run as and read (credentials, endpoints, model) |

Terraform (`terraform/`) owns everything outside the cluster: the cluster itself, Google service accounts and IAM, BigQuery,
Firestore, the container registry and the build identity.

## 4. Request lifecycle

The step numbers in the diagram (1 to 8) map to this section as follows: steps 1 to 4 are §4.1 (getting a token), step 5 is
§4.2 items 1 to 3 and §5.1 to §5.3, step 6 is §4.2 items 4 to 8 and §5.5 to §5.6, step 7 is §4.2 item 9 and §6, and step 8 is
§10. Two HTTP status codes appear throughout: **401 Unauthorized** means the gateway cannot tell who is calling (no token, or an
invalid or expired one); **403 Forbidden** means it knows who is calling but that role is not allowed.

### 4.1 Getting a token and discovering where to authenticate

```
 client                         gateway (proxy)                     Keycloak
   │  POST /mcp (no token)           │                                  │
   │────────────────────────────────►│ JWT policy: no bearer token      │
   │◄────────────────────────────────│ 401  WWW-Authenticate: Bearer    │
   │   resource_metadata=".../.well-known/oauth-protected-resource/mcp" │
   │  GET that URL                   │                                  │
   │────────────────────────────────►│ answered by the gateway itself   │
   │◄────────────────────────────────│ {resource, authorization_servers, scopes_supported}
   │  GET /.well-known/oauth-authorization-server/mcp                   │
   │────────────────────────────────►│── GET {issuer}/.well-known/openid-configuration ─►│
   │◄────────────────────────────────│◄─ endpoints (token, authorize, jwks …) ───────────│
   │         (gateway returns the metadata with its own issuer and registration endpoint)  │
   │  POST /realms/mcp/protocol/openid-connect/token                    │
   │  grant_type=client_credentials, client_id, client_secret ─────────────────────────►│
   │◄─────────────────────────────────────────────── access_token (JWT, 300 s) ─────────│
```

The agent in this repository is configured with the token endpoint and client credentials directly, so it skips the
discovery requests; `tests/test_flow.py` performs and asserts every step of the discovery exchange.

### 4.2 An authenticated tool call

```
 client ──► proxy ─────────────────────────────────► server ───────────────► data store
   │ Authorization: Bearer <JWT>
   │            1 verify JWT signature, iss, aud, exp   (else 401)
   │            2 require a role claim                  (else 403)
   │            3 tools/list: merge servers' lists, drop tools the role may not use
   │              tools/call: refuse a tool the role may not use ("Unknown tool")
   │            4 pick the target from the tool-name prefix
   │            5 mint an ID token for audience mcp-<server> (GKE metadata server)
   │            6 call http://mcp-<server>.mcp-platform.svc.cluster.local:8000/mcp
   │                                  7 verify Google signature, iss, aud, exp,
   │                                    email_verified, email == gateway account
   │                                  8 validate arguments, run the tool,
   │                                    write one audit line
   │                                  9 call BigQuery / Firestore / K8s API
   │                                    as the server's own Google identity
   │◄────────── result ◄──────────────◄──────────────────────────────────────
```

### 4.3 What each failure looks like

| Situation | Rejected by | Response | Evidence |
|---|---|---|---|
| No token, garbage token, expired, tampered, signed by an unknown key, wrong audience | gateway JWT validation | `401`; log line `mcp authentication failure` | gateway log |
| Valid token, right audience, **no role** | gateway route authorization | `403 authorization failed` | gateway log |
| Valid role, tool not allowed for it | gateway tool rule | tool absent from `tools/list`; direct call → JSON-RPC "Unknown tool" (HTTP 400 in the log) | gateway log |
| Allowed tool, bad argument (unknown namespace, invalid priority, malformed id) | the server's tool | tool error result | server audit line `outcome: error:…` |
| Anyone calling a server directly without the gateway's identity | server middleware | `401` (or `403` for a valid token from another Google identity); `/healthz` is the only open path | server audit line `request_rejected` |
| A pod that is not the gateway connecting to a server | NetworkPolicy | connection times out | not logged (dropped) |

## 5. How each check works

### 5.1 JWT validation (gateway)

`AgentgatewayPolicy mcp-authn` is attached to the HTTPRoute. In `jwtAuthentication`:

* `mode: Strict`: a valid token is mandatory.
* One provider with the realm's `issuer` (must equal the token's `iss`), the accepted `audiences` (the gateway's
  localhost URL and its in-cluster URL; the token's `aud` must contain one), and a **remote JWKS**: the controller fetches
  `/realms/mcp/protocol/openid-connect/certs` from the Keycloak Service. The token header's `kid` selects the key; the RS256
  signature, `exp` and `nbf` (60 s leeway) are checked.
* Because verification uses cached public keys, the proxy never calls Keycloak per request.

The `mcp:` block of the same policy turns on the MCP authorization behaviour: provider-specific adaptations for Keycloak,
the protected-resource metadata document (`resource`, `scopesSupported`, `bearerMethodsSupported`), the
`WWW-Authenticate: Bearer resource_metadata=…` header on `401`, and the authorization-server metadata endpoint.

### 5.2 Role requirement (gateway)

The same policy carries a route-level CEL rule evaluated after authentication:

```
has(jwt.roles) && jwt.roles.exists(r, r == "mcp-viewer" || r == "mcp-operator")
```

A token with no `roles` claim (for example one issued to a client without the `mcp-gateway` scope, or to a service account
with no role) is authenticated but denied with `403`.

### 5.3 Per-tool authorization (gateway)

`AgentgatewayPolicy tool-rbac` is attached to the backend and holds `backend.mcp.authorization` with `action: Allow`
and a list of CEL expressions; a request is allowed if **any** expression matches, otherwise it is denied:

```
jwt.roles.exists(r, r == "mcp-operator")                                   # operator: any tool
jwt.roles.exists(r, r == "mcp-viewer") && mcp.tool.target == "k8s"
    && mcp.tool.name in ["list_pods", "get_pod_events", "get_pod_logs"]    # one rule per server for viewers
```

`mcp.tool.target` is the server (target name) and `mcp.tool.name` is that server's **own** tool name, not the prefixed name
clients see. The viewer rules are generated from `viewerTools` in `values.yaml`, so adding a tool to the viewer role is an
explicit change; a tool nobody lists (for example a new write tool) is invisible to viewers. The rule is applied to
`tools/list` (denied tools are removed from the response) and to `tools/call`.

### 5.4 Federation and naming

The backend has three static targets (`k8s`, `finops`, `tickets`). On `tools/list` the proxy queries every target, prefixes
each tool name with its target (`<target>_<tool>`, which keeps names unique across servers), filters by the rule above and
returns one merged list. On `tools/call` it strips the prefix to find the target and forwards the call there. Adding a server
means adding an entry under `servers:` in `values.yaml`; clients keep using the same single URL.

### 5.5 Gateway → server credential

Each target has `policies.auth.gcp` with `type: IdToken` and an explicit `audience` (`mcp-k8s`, `mcp-finops`,
`mcp-tickets`). To obtain the token the proxy asks the **GKE metadata server** for an identity token for its own Google
service account. This works because the proxy's Kubernetes ServiceAccount (`mcp-gateway`) is annotated
`iam.gke.io/gcp-service-account: mcp-gateway@…` and Terraform grants `roles/iam.workloadIdentityUser` on that Google account
to `serviceAccount:<project>.svc.id.goog[mcp-platform/mcp-gateway]`. The result is a JWT signed by Google:

```
iss: https://accounts.google.com     aud: mcp-tickets     email: mcp-gateway@<project>.iam.gserviceaccount.com
email_verified: true                 exp: set by Google (typically one hour)
```

The audience is per target, so a token minted for one server is rejected by every other. Selector-based targets cannot be
used here because there is no hostname to derive an audience from; that is why the targets are static (host + port) with an
explicit audience.

### 5.6 Server-side verification

`servers/app/mcpkit/` wraps each server in an ASGI middleware. For every request except `GET /healthz` it:

1. requires `Authorization: Bearer …`;
2. verifies the signature against Google's published certificates (fetched from `googleapis.com`, cached for an hour,
   refetched once if the token's key id is unknown, which covers key rotation);
3. checks the issuer is Google, the token has not expired (10 s skew allowed), and the **audience equals this server's
   audience**;
4. requires `email_verified` and that `email` equals the gateway's service account (`GATEWAY_EMAIL`).

A failed check never reaches a tool: no credential, a bad or wrong-audience token → `401`; a valid Google token from another
identity → `403`. The response body is generic; the reason goes to the audit log. The unit tests in `servers/tests/` cover
each branch, and `tests/test_isolation.py` repeats the most important ones against the live servers, including a genuine
Google-signed token minted for a different audience.

## 6. Identities and permissions

Every workload that touches a Google API has its own Google service account, bound to its Kubernetes ServiceAccount with
Workload Identity. No service-account keys exist (the evidence script lists the keys per account: zero).

| Kubernetes ServiceAccount | Google service account | Permissions |
|---|---|---|
| `mcp-gateway` | `mcp-gateway` | none on any resource; used only to mint ID tokens for the servers |
| `mcp-k8s` | none | Kubernetes `Role`: get/list pods, get pod logs, get/list events, namespace `mcp-platform` only |
| `mcp-finops` | `mcp-finops` | `bigquery.dataViewer` on dataset `mcp_finops`; `bigquery.jobUser` on the project |
| `mcp-tickets` | `mcp-tickets` | `datastore.user` on the project, **conditioned** on `resource.name == projects/<p>/databases/<tickets db>` |
| `mcp-agent` | `mcp-agent` | `aiplatform.user` (call Gemini); nothing else |
| (build) | `mcp-build` | read its own source bucket, write the one Artifact Registry repository, write logs |
| (nodes) | project default compute account | read access on the one Artifact Registry repository (to pull images) |

## 7. Keycloak

Keycloak runs in development mode and imports the realm from `realm.json` (a Secret rendered from
`deploy/chart/files/realm.json.tpl`) when it starts. Because Keycloak reads the realm only at start-up, the Deployment
carries a `checksum/realm` annotation so that any realm change rolls the pod.

The realm `mcp` defines:

* **Roles** `mcp-viewer` and `mcp-operator`.
* **A client scope `mcp-gateway`** with three protocol mappers: two *audience* mappers (add the gateway's localhost and
  in-cluster URLs to `aud`) and a *realm-role* mapper that writes the user's roles to a top-level `roles` claim.
* **Four confidential clients** with service accounts and client-credentials only (no browser flows):
  `agent-viewer` (role viewer), `agent-operator` (role operator), `agent-norole` (right audience, no role) and
  `agent-noaud` (a role but no `mcp-gateway` scope, so its token is not addressed to the gateway). The last two exist so
  the tests can prove the gateway rejects tokens that are valid but not sufficient.
* **A pinned signing key.** A custom `rsa` key provider (priority 1000) carries a private key and certificate generated once
  by `up.sh` into `.secrets/` and injected through the chart. Without it Keycloak generates a new key on every start, the
  gateway's cached key set goes stale, and valid tokens fail until the cache expires. Declaring a custom key provider also
  suppresses Keycloak's default HMAC and AES providers, so those are declared explicitly.
* `accessTokenLifespan: 300` (tokens live five minutes) and `sslRequired: none` (traffic is cluster-internal HTTP).
* `KC_HOSTNAME` is fixed to Keycloak's in-cluster address, so the `iss` claim is identical whether the token was requested
  through the Service or a port-forward. The gateway fetches Keycloak's metadata from that same address, so it must be
  reachable from inside the cluster.

A decoded access token for `agent-viewer`:

```json
{ "iss": "http://keycloak.mcp-platform.svc.cluster.local:8080/realms/mcp",
  "aud": ["http://mcp-gateway.mcp-platform.svc.cluster.local/mcp", "http://localhost:18080/mcp"],
  "azp": "agent-viewer", "scope": "mcp-gateway", "roles": ["mcp-viewer"], "exp": 1791657744 }
```

## 8. Network policy

The namespace is default-deny for ingress. `deploy/chart/templates/netpol.yaml` and `agent.yaml` add the exceptions:

| Policy | Selects | Allows |
|---|---|---|
| `default-deny-ingress` | all pods | nothing in |
| `gateway-to-servers` | MCP servers | ingress on 8000 from the gateway pods only |
| `keycloak-ingress` | Keycloak | ingress on 8080 from the `agentgateway-system` namespace (key fetch), the gateway pods (metadata) and pods labelled `mcp-client` |
| `clients-to-gateway` | gateway pods | ingress on 80 from pods labelled `mcp-client` (the agent) |
| `servers-egress` | MCP servers | DNS, the GKE metadata server (`169.254.169.252:988`, `169.254.169.254:80`), and HTTPS (443) |
| `gateway-egress` | gateway pods | DNS, metadata server, the controller (`agentgateway-system:9978`), the servers (8000), Keycloak (8080) |
| `agent-egress` | agent pods | DNS, metadata server, the gateway (80), Keycloak (8080), HTTPS (443 for Vertex AI) |

Consequences: a pod that is not the gateway cannot reach a server even from inside the namespace; the agent cannot reach any
server directly; a server cannot make plain-HTTP or SMTP connections. Probes from the node and `kubectl port-forward`
traffic are not subject to these policies, which is how the tests reach services from outside the cluster.

## 9. The MCP servers

All three share one container image (`servers/Dockerfile`, Python 3.13, non-root user 10001). `main.py` reads `SERVER`
(`k8s`, `finops`, `tickets`), loads the matching module, builds the MCP app with the stateless streamable-HTTP transport and a
Host-header allow-list (DNS-rebinding protection), and wraps it in the verification middleware. Pods run as non-root with a
read-only root filesystem, all capabilities dropped and the default seccomp profile; only the `k8s` pod mounts a Kubernetes
API token.

Every tool is wrapped by an audit decorator that writes one JSON line per call:

```json
{"ts":"2026-10-10T19:18:59Z","event":"tool_call","server":"tickets","tool":"create_ticket",
 "caller":"mcp-gateway@<project>.iam.gserviceaccount.com","arg_names":["description","priority","title"],"outcome":"ok","ms":41}
```

Argument values are deliberately not logged; rejected requests produce `request_rejected` lines with the reason.

| Server | Behaviour and limits |
|---|---|
| `k8s` | Calls the Kubernetes API in-cluster. Only namespaces in `ALLOWED_NAMESPACES` are accepted, in addition to the `Role` limiting what the API will return. `get_pod_logs` caps the tail at 200 lines; `get_pod_events` returns the last 20; `list_pods` hides finished Job pods unless `include_completed` is true. |
| `finops` | Two fixed, parameterised BigQuery queries over `mcp_finops.billing` (`usage_date, service, sku, project, cost`). `days` is clamped to 1–365 and `limit` to 1–50; each query has a 50 MB billing cap. The table holds 1,440 synthetic rows (90 days × 2 projects × 8 SKUs) created by `scripts/seed-finops.sh`. |
| `tickets` | Firestore database `mcp-tickets<suffix>`, collection `tickets`. Document fields: `title` (1–120 chars), `description` (≤ 2,000), `priority` (`low`/`medium`/`high`), `status` (`open`/`in_progress`/`closed`), `created_at`, `updated_at`. Ids are UUIDs and are validated. The suffix is per deployment because Firestore does not allow a deleted database name to be reused immediately. |

## 10. The agent

`agent/app/agent.py` is a deliberately small loop so that every step is visible:

1. Request an access token from Keycloak with the client credentials supplied in the environment (a Secret).
2. Open an MCP session to the gateway and call `tools/list`. The result is already filtered for this identity.
3. Declare those tools to Gemini (`gemini-2.5-flash` on Vertex AI, `europe-west1`) using each tool's JSON schema; automatic
   function calling is disabled so that this code, not the SDK, executes calls.
4. Loop (at most 8 steps): send the conversation, and for every function call the model returns, call the tool through the
   gateway and send the result back. Results are truncated to 6,000 characters and labelled as data, not instructions.
5. Emit one JSON event per step (`tools_available`, `tool_call`, `tool_result`, `answer`); `tests/transcript.py` renders them.

The agent runs as a Kubernetes Job (`scripts/ask.sh`) with labels `app=mcp-agent` and `mcp-client=true`, which is what the
network policies key on, and with the Kubernetes ServiceAccount `mcp-agent` (Workload Identity → Vertex AI only).

## 11. Build and deploy

### Terraform and Helm

| Tool | Owns |
|---|---|
| Terraform (`terraform/`, state in a GCS bucket) | Cluster, APIs, Google service accounts and IAM, Workload Identity bindings, BigQuery dataset, Firestore database, Artifact Registry, build bucket and build identity (31 resources) |
| Helm (`deploy/chart/`) | Everything inside `mcp-platform`: Keycloak and realm, servers, gateway objects, policies, network policies, RBAC, agent resources |
| Helm (upstream charts) | agentgateway CRDs and controller, version-pinned in `scripts/lib.sh` |

GKE already ships the Gateway API CRDs (v1.6.0, inside agentgateway's supported range), so they are not installed again.

### What `up.sh` does, and why in this order

1. Create a Python virtualenv for the test tooling.
2. Terraform: state bucket, then plan and apply the saved plan (cluster, identities, data stores, registry).
3. Fetch cluster credentials.
4. Install the agentgateway CRDs and controller with Helm.
5. Build both images with Cloud Build (tag = hash of the sources, so unchanged code is not rebuilt), seed the BigQuery table,
   generate secrets, and install the chart **with the policies disabled**; wait for Keycloak, the servers and the gateway.
6. Upgrade the chart with the policies enabled. Policies come last because the controller fetches Keycloak's key set the
   moment a policy referencing it appears; Keycloak must already be serving it.

`down.sh` uninstalls the chart, deletes the namespaces (which releases any load balancer), plans and applies the Terraform
destroy, and verifies that no cluster, forwarding rule or reserved address remains.

### Image builds

Cloud Build runs as the dedicated `mcp-build` service account, reading sources from a private bucket and pushing to the one
registry repository. Nodes pull with the project's default compute service account, granted read access on that repository.

## 12. Design decisions

| Decision | Reason |
|---|---|
| agentgateway as the gateway | Built on the Kubernetes Gateway API; native MCP federation, per-tool CEL authorization, MCP authorization metadata and per-target backend authentication; tracks the current MCP specification; Apache 2.0. (Alternatives such as IBM ContextForge and Docker's MCP gateway were evaluated from documentation only; only agentgateway was built and tested.) |
| Authorization at the gateway, default deny | One place to change who can use which tool; a tool nobody listed is invisible to viewers rather than silently allowed. |
| Gateway authenticates to servers as itself | The MCP specification forbids token passthrough (confused deputy). A per-server audience means a token for one server is useless against another. Keyless via Workload Identity: nothing to rotate or leak. |
| Servers verify the gateway even though the network restricts access | Defence in depth: network policy and token verification fail independently, and each is tested with the other bypassed. |
| Static targets with explicit audiences | Selector targets are IP-based and cannot mint an audience-bound ID token; static targets also give clean tool names. |
| One Google service account per workload with resource-level grants | A compromised `finops` server reaches one BigQuery dataset, not the project; the Firestore grant names its one database. |
| Issuer = Keycloak's in-cluster address with a fixed hostname | The gateway must fetch IdP metadata from the issuer URL; a fixed hostname keeps `iss` identical for every client. |
| Audience = the gateway's URL, enforced | A token for another service, or with no audience, is rejected even though Keycloak signed it. |
| Pinned Keycloak signing key | See §13: restarts and node upgrades otherwise invalidate the gateway's cached keys. |
| Two agent identities, same code | Permission lives in the token, enforced at the gateway, not in a prompt. |
| Plain agent loop instead of an agent framework | The whole flow (token → tools → model → call → result) is visible and testable. |
| Gateway Service is `ClusterIP` | No public listener; access is by port-forward until a TLS entry point exists. |
| Dedicated build identity with three permissions | The default Cloud Build identity is far broader. |
| Helm chart plus Terraform, not one tool | Cloud resources and in-cluster objects have different lifecycles and different review needs. |

## 13. Failure behaviour and operational notes

| Behaviour | Explanation | What to do |
|---|---|---|
| Valid tokens return `401` with `token uses the unknown key` | The gateway cached an old Keycloak key set. Normally impossible because the key is pinned; it happens if `.secrets/realm-key.pem` was regenerated | Delete and recreate `mcp-authn` (`up.sh` does this) or wait out the 5-minute cache |
| Realm change has no effect | Keycloak imports its realm only at start-up | The chart rolls the pod on a realm change; otherwise `rollout restart deploy/keycloak` |
| Keycloak restart loses runtime state | Development mode with an in-memory database | Realm and keys are re-imported from the Secret, so tokens keep validating; sessions are lost |
| Pods are rescheduled during a node upgrade | Autopilot upgrades and compacts nodes on its own schedule | Expected; the resilience tests cover Keycloak and the gateway pod |
| Gateway returns `500 idToken auth requires a hostname target or explicit audience` | A selector target was used with ID-token auth | Use a static host target with an explicit audience |
| Viewer sees no tools after a policy edit | In a rule `mcp.tool.name` is the server's own tool name; the prefixed name never matches | Match `mcp.tool.target` and the raw name |
| Discovery endpoint returns `500` after tightening network policy | The proxy fetches Keycloak's metadata at request time | Allow proxy → Keycloak (8080) and Keycloak ingress from the proxy |
| `ImagePullBackOff` on server pods | Nodes pull as the default compute service account | It needs `artifactregistry.reader` on the repository (Terraform `nodes_pull`) |
| Autopilot cannot change its node service account | Fixed at cluster creation | Create a new cluster if a dedicated node account is required |
| A tool returning a `dict` arrives as one JSON text item; `list[dict]` as structured content | MCP SDK serialization | The test helper normalizes both |
| Finished Job pods shown as "not ready" by an agent | `list_pods` returned them | Fixed: completed pods are hidden by default |
