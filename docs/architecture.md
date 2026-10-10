# Architecture

## Request path (today)
1. Client sends an MCP request with `Authorization: Bearer <JWT>` to the gateway's `/mcp`.
2. The proxy validates the JWT (issuer, audience, expiry, signature against the JWKS in the policy).
   Failure: HTTP 401.
3. The proxy evaluates the backend authorization rule (CEL) with the token's claims and the requested tool.
   `tools/list` is filtered to what the caller may use; a call to anything else is "Unknown tool".
4. Allowed calls are routed to the server that owns the tool. `tools/list` is the merge of all servers.

Components: `agentgateway` control plane (watches Gateway API + policy objects, programs the proxy),
`agentgateway-proxy` (the data plane, a `LoadBalancer` Service), `AgentgatewayBackend mcp` (federates the servers
by label selector), `HTTPRoute /mcp`, two `Deployment`s (`mcp-alpha`, `mcp-beta`; stateless streamable HTTP).

## Decisions
| Decision | Why |
|---|---|
| agentgateway over IBM ContextForge, Docker MCP Gateway, managed gateways | Gateway API native, MCP federation and per-tool CEL RBAC built in, tracks the current MCP spec, Apache 2.0. ContextForge is labelled alpha/beta by IBM and needs Postgres + Redis. (Comparison came from vendor write-ups; only agentgateway was tested here.) |
| GKE Autopilot | No node management; Workload Identity, Shielded Nodes, Dataplane V2 (NetworkPolicy) on by default. |
| Use GKE's own Gateway API CRDs | GKE ships v1.6.0 (inside agentgateway's supported 1.4 to 1.6 range); installing a second copy would fight the managed one. |
| Stateless MCP servers | MCP 2026-07-28 removed sessions; no sticky routing, any replica serves any call. |
| Authorization at the gateway, by token claim | One place to change who can use which tool; servers stay simple. Defence in depth (server-side checks) is planned. |

## Not built yet (target)
Keycloak as issuer with MCP protected-resource metadata; three real servers (k8s read-only ops, BigQuery FinOps,
Firestore tickets); Workload Identity per server; NetworkPolicy default-deny and a private VPC; an ADK/Gemini agent;
audit logging and tracing. The gateway should exchange tokens for downstream calls rather than pass the caller's
token through (the MCP spec forbids passthrough).

## Regenerating the diagram
`python3 docs/diagrams/architecture.py && python3 docs/diagrams/render.py docs/img/architecture.svg docs/img/architecture.png`
(render.py needs `pip install playwright` and Google Chrome.)
