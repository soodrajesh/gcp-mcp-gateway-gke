# Architecture

## Request path (today)
1. Client calls `/mcp` with no token → **401** with `WWW-Authenticate: Bearer resource_metadata="<gateway>/.well-known/oauth-protected-resource/mcp"`.
2. Client reads the gateway's **protected-resource metadata** (names the resource and the scope) and **authorization-server metadata** (the gateway fetches Keycloak's, rewrites it and serves it, so clients and Keycloak need no direct trust setup).
3. Client authenticates at **Keycloak** (client-credentials for a service agent) and receives a **JWT**: `iss` = the realm, `aud` = this gateway, `roles` = its role(s).
4. Client repeats the request with `Authorization: Bearer <JWT>`.
5. The proxy verifies signature (keys the controller fetched from Keycloak), issuer, audience and expiry (401 on failure), then requires a role (403 without). The backend rule then limits which *tools* that role may use: `tools/list` omits the rest and a direct call is "Unknown tool".
6. Allowed calls are routed to the server that owns the tool; `tools/list` is the merge of all servers.

Components (namespace `mcp-platform`, deployed by the Helm chart in `deploy/chart`): `keycloak` (IdP, realm imported from a Secret),
`mcp-gateway` (the proxy the controller creates for the `Gateway`), `AgentgatewayBackend mcp` (federates the servers by label),
`HTTPRoute mcp` (`/mcp` plus the two `.well-known` discovery paths), `AgentgatewayPolicy mcp-authn` (JWT + MCP discovery + role)
and `tool-rbac` (per-tool CEL), `mcp-alpha`/`mcp-beta` (stateless streamable HTTP). Namespace `agentgateway-system`: the controller.

## Decisions
| Decision | Why |
|---|---|
| agentgateway over IBM ContextForge, Docker MCP Gateway, managed gateways | Gateway API native, MCP federation and per-tool CEL RBAC built in, tracks the current MCP spec, Apache 2.0. ContextForge is labelled alpha/beta by IBM and needs Postgres + Redis. (Comparison came from vendor write-ups; only agentgateway was tested here.) |
| GKE Autopilot | No node management; Workload Identity, Shielded Nodes, Dataplane V2 (NetworkPolicy) on by default. |
| Keycloak as the IdP (not a hand-made key) | A real authorization server: client registry, roles-as-claims, audience mapper, key rotation. The realm is code (`realm.json.tpl`), so it is reviewable and rebuilt on every deploy. |
| Issuer = Keycloak's in-cluster URL | The gateway fetches the IdP's metadata from the issuer URL, so it must resolve from inside the cluster. Keycloak pins `iss` to that value (`KC_HOSTNAME`), so a token fetched through a laptop port-forward carries the same issuer. |
| Audience = the gateway's URL, enforced | A token for another service (or no audience) is rejected, even though Keycloak signed it. Tested with `agent-noaud`. |
| Policies applied after Keycloak is serving | The controller fetches the key set when the policy appears; installing Keycloak first avoids a failed first fetch. |
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
