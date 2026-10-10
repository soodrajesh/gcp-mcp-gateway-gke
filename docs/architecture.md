# Architecture

## Request path (today)
1. Client calls `/mcp` with no token → **401** with `WWW-Authenticate: Bearer resource_metadata="<gateway>/.well-known/oauth-protected-resource/mcp"`.
2. Client reads the gateway's **protected-resource metadata** (names the resource and the scope) and **authorization-server metadata** (the gateway fetches Keycloak's, rewrites it and serves it, so clients and Keycloak need no direct trust setup).
3. Client authenticates at **Keycloak** (client-credentials for a service agent) and receives a **JWT**: `iss` = the realm, `aud` = this gateway, `roles` = its role(s).
4. Client repeats the request with `Authorization: Bearer <JWT>`.
5. The proxy verifies signature (keys the controller fetched from Keycloak), issuer, audience and expiry (401 on failure), then requires a role (403 without). The backend rule then limits which *tools* that role may use: `tools/list` omits the rest and a direct call is "Unknown tool".
6. Allowed calls are routed to the server that owns the tool; `tools/list` is the merge of all servers.
7. **Gateway → server, as itself.** Each target carries its own credential: a Google-signed ID token minted from the gateway pod's Workload Identity, with `aud = mcp-<server>`. The caller's token is never forwarded. The server verifies signature, issuer, audience, expiry, `email_verified` and that the caller is the gateway's service account (401/403 otherwise), then runs the tool.
8. **Server → Google API, as itself.** Each server's Kubernetes ServiceAccount is bound to its own Google service account with the narrowest role that works (one dataset, one Firestore database, one namespace's pods). No keys exist.

Components (namespace `mcp-platform`, deployed by the Helm chart in `deploy/chart`): `keycloak` (IdP, realm imported from a Secret),
`mcp-gateway` (the proxy the controller creates for the `Gateway`), `AgentgatewayBackend mcp` (federates the servers by label),
`HTTPRoute mcp` (`/mcp` plus the two `.well-known` discovery paths), `AgentgatewayPolicy mcp-authn` (JWT + MCP discovery + role)
and `tool-rbac` (per-tool CEL), `mcp-k8s`, `mcp-finops`, `mcp-tickets` (stateless streamable HTTP, one image, `SERVER` selects the behaviour), six NetworkPolicies and a read-only `Role`. Namespace `agentgateway-system`: the controller.

9. **The AI agent** is a short-lived Job (label `mcp-client`, its own Kubernetes ServiceAccount bound to a Google service account whose only role is `aiplatform.user`). It performs steps 1-6 as `agent-viewer` or `agent-operator`, lists the tools the gateway offers *to that identity*, hands them to Gemini, and executes the calls the model asks for. It has no route to the servers and holds no credential for them.

## Decisions
| Decision | Why |
|---|---|
| agentgateway over IBM ContextForge, Docker MCP Gateway, managed gateways | Gateway API native, MCP federation and per-tool CEL RBAC built in, tracks the current MCP spec, Apache 2.0. ContextForge is labelled alpha/beta by IBM and needs Postgres + Redis. (Comparison came from vendor write-ups; only agentgateway was tested here.) |
| GKE Autopilot | No node management; Workload Identity, Shielded Nodes, Dataplane V2 (NetworkPolicy) on by default. |
| Keycloak as the IdP (not a hand-made key) | A real authorization server: client registry, roles-as-claims, audience mapper, key rotation. The realm is code (`realm.json.tpl`), so it is reviewable and rebuilt on every deploy. |
| Issuer = Keycloak's in-cluster URL | The gateway fetches the IdP's metadata from the issuer URL, so it must resolve from inside the cluster. Keycloak pins `iss` to that value (`KC_HOSTNAME`), so a token fetched through a laptop port-forward carries the same issuer. |
| Audience = the gateway's URL, enforced | A token for another service (or no audience) is rejected, even though Keycloak signed it. Tested with `agent-noaud`. |
| Policies applied after Keycloak is serving | The controller fetches the key set when the policy appears; installing Keycloak first avoids a failed first fetch. |
| Gateway→server: Google ID token per target (keyless), not token passthrough | The MCP spec forbids passthrough (confused deputy). An ID token with a per-server audience means a token minted for one server is useless against another. Keyless via Workload Identity: nothing to rotate or leak. Alternative considered: RFC 8693 token exchange at Keycloak (keeps end-user identity, more moving parts); not done. |\n| Static targets (host + explicit audience), not label selectors | Selector targets are IP-based and cannot mint an audience-bound ID token. Static targets also give clean tool names (`tickets_create_ticket`). |\n| One Google service account per workload, resource-level grants | Compromising `mcp-finops` yields one BigQuery dataset, not the project. The Firestore grant carries an IAM condition naming the one database. |\n| Servers verify the gateway even though the network already restricts them | Defence in depth: NetworkPolicy and token verification fail independently. Both are tested, directly, with the gateway bypassed. |\n| Viewer allow-list is explicit, default deny | A tool nobody listed (for example a new write tool) is invisible to viewers instead of silently allowed. |\n| Gateway Service is `ClusterIP` (via `AgentgatewayParameters`) | No public listener; reached through port-forward until a TLS entry point is built. |\n| Dedicated build service account (3 permissions) | The default Cloud Build identity is far broader. Build is reproducible: image tag = hash of the sources, so unchanged code is never rebuilt. |\n| The agent is a plain loop (Gemini SDK + MCP client), not an agent framework | The whole flow is visible and testable: token → tool list → model → call → result. Tool output goes back to the model labelled as data, and the system prompt says never to follow instructions found in it. |
| Two agent identities (viewer / operator) with the same code | What the agent may do is decided by its token at the gateway, not by its prompt. A prompt-injected viewer agent has no write tool to be talked into calling. |
| Keycloak's signing key is pinned in the realm (generated once, kept as a Secret) | Keycloak in dev mode generates a new key on every start, and the gateway caches the key set for at least 5 minutes: any restart or node upgrade made valid tokens fail with 401 until the policy was re-applied. Hit live during a node upgrade. A pinned key makes restarts invisible (tested by killing the pod). |
| A checksum annotation rolls Keycloak when the realm changes | Keycloak reads its realm only at start-up; without it a changed Secret silently changed nothing. |
| Use GKE's own Gateway API CRDs | GKE ships v1.6.0 (inside agentgateway's supported 1.4 to 1.6 range); installing a second copy would fight the managed one. |
| Stateless MCP servers | MCP 2026-07-28 removed sessions; no sticky routing, any replica serves any call. |
| Authorization at the gateway, by token claim | One place to change who can use which tool; servers stay simple. Defence in depth (server-side checks) is planned. |

## Not built yet (target)
Three real servers (k8s read-only ops, BigQuery FinOps,
Firestore tickets); Workload Identity per server; NetworkPolicy default-deny and a private VPC; an ADK/Gemini agent;
audit logging and tracing. The gateway should exchange tokens for downstream calls rather than pass the caller's
token through (the MCP spec forbids passthrough).

## Regenerating the diagram
`python3 docs/diagrams/architecture.py && python3 docs/diagrams/render.py docs/img/architecture.svg docs/img/architecture.png`
(render.py needs `pip install playwright` and Google Chrome.)

## Lessons from building it (so far)

* **A test that only checks "denied" can pass for the wrong reason.** The first run of the "valid token, no role" check passed with 401 while *every* token was being rejected (stale signing keys). A positive control ("the viewer's real token is accepted") exposed it; the check now returns the true answer, 403.
* **The controller, not the proxy, fetches the IdP's keys**, and caches them (minimum 5 minutes). A Keycloak restart with an in-memory database rotates keys; plan for that in production with a persistent database and a rotation policy.
* **The issuer must be reachable by the gateway**, because it fetches the IdP metadata from that URL; a `localhost` issuer failed with a 500.
* **A resource-metadata `resource` value, the token `aud`, and the gateway's `audiences` must agree exactly**, or valid tokens are rejected.
* **`mcp.tool.name` in an authorization rule is the server's own tool name, not the prefixed name clients see**; `mcp.tool.target` is the server. A rule written against the prefixed name hides every tool from the viewer (found live).
* **The gateway proxy, not only the controller, talks to Keycloak** (metadata at request time); a default-deny egress policy on the proxy broke discovery until Keycloak was allowed. The isolation tests exist so this class of regression is caught.
* **Selector targets cannot use `idToken` backend auth** (no hostname to derive an audience from): switch to static targets with an explicit per-target audience.
* **Image pulls fail with `ImagePullBackOff` until the nodes' service account may read the repository**; an Autopilot cluster cannot change its node service account in place.
* **A tool that returns a `dict` arrives as one JSON text item; `list[dict]` arrives as structured content**: the test helper normalises both.
* **Keycloak imports its realm only at start-up.** A Secret change without a pod restart changes nothing (I tested the old pod and believed the new config was live). Roll the pod on realm changes (checksum annotation).
* **A custom key provider in the realm import replaces Keycloak's default HMAC/AES providers**, which it only creates when none exist; declare them explicitly. The provider also needs `"subComponents": {}`.
* **A node upgrade is a normal event on Autopilot**: Keycloak and the gateway were rescheduled mid-session. Anything that only survives while a pod lives (in-memory keys, cached state) will eventually break; the resilience suite exists for that.
* **Positive controls can flake right after a restart** (a cold gateway pod); they retry, blocked-probes never do.
* **A test that cleans up with `pkill -f` can kill an unrelated port-forward.** Start helpers with `exec` and terminate your own process handle.
* **`k8s_list_pods` hid nothing at first, so the agent reported finished Job pods as "not ready".** Tool design matters for agents: the default now hides completed pods.
