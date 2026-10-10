# Spike findings (2026-10-10)

Throwaway GKE Autopilot cluster (europe-west1), agentgateway v1.6.0, two stateless MCP servers
(Python SDK `mcp` 2.3.0, `MCPServer`, `stateless_http=True`). Captured output, not expected output.

| # | Question | Result |
|---|---|---|
| 1 | Does agentgateway 1.6.0 install on Autopilot (k8s 1.36)? | Yes. `helm upgrade -i` of the CRDs and the chart; control plane Running in ~90 s. |
| 2 | Gateway API CRDs? | GKE already ships v1.6.0 standard CRDs (`components.gke.io/...gateway-api-crds`). Do not install your own: agentgateway 1.6 supports 1.4-1.6. |
| 3 | Federation: one endpoint, two servers? | Yes. One `tools/list` returned both servers' tools; calls routed to the right server. |
| 4 | Does the 2.3 SDK client talk to the gateway on 2026-07-28? | Yes (client `mode=auto`, stateless servers, no session affinity needed). |
| 5 | JWT validation | No token and garbage token: HTTP 401. Valid token: allowed. |
| 6 | Per-tool RBAC from a token claim (CEL) | `viewer`: `tools/list` shows only `*_read_thing`; calling `write_thing` -> `Unknown tool`. `operator`: all four tools; write succeeds. |

## Gotchas that change the design
* **Tool prefix is `<service>-<port>_`** (`mcp-alpha-8000_read_thing`) for selector targets, not the target `name`.
  Static targets (named) or `prefixMode` give clean names. Decide in the build.
* **Plain `jwtAuthentication` returns 401 with no `WWW-Authenticate: ... resource_metadata=`.** The MCP spec's
  discovery needs the `mcp:` block (protected-resource metadata) on the policy; a spec-following client cannot
  find the authorization server without it. Use MCP auth in the real build.
* A bare `curl` JSON-RPC POST returns 400 with an empty body even with a valid token (request shape for
  2026-07-28 is not the old one). Use the SDK client for tests, not hand-rolled JSON-RPC.
* Python SDK 2.x renamed `FastMCP` -> `MCPServer`; DNS-rebinding protection must be configured for in-cluster Host headers.
* `tool-rbac` hides denied tools from `tools/list` and answers a direct call with "Unknown tool" (not 403).
  Tests must assert both.

## Not yet verified
Keycloak as the issuer on Autopilot; `mcp:` auth block fields; OTel export; NetworkPolicy interaction; real cost/day.
