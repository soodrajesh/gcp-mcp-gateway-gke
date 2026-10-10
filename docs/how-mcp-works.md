# How MCP, gateways, OAuth and JWT fit together

Written for someone who wants the whole picture before the code. Items marked **[demo]** you can run today in
this repo (`./scripts/up.sh`, then `spike/cli.py`); items marked **[planned]** are the next build.

## 1. The pieces

```
 User ──► AI app (host) ──► MCP client ──► MCP gateway ──► MCP server ──► real system
          (LLM inside)      (one per          (policy)       (tools)       (k8s, BigQuery,
                             connection)                                    ticket DB)
                                 ▲                 ▲
                                 └── tokens ◄── Identity provider (IdP / authorization server)
```

| Piece | What it is | Example here |
|---|---|---|
| **LLM** | Decides *which* tool to call and with what arguments. Never calls anything itself. | Gemini (planned) |
| **Host / agent** | The app that runs the LLM and executes its tool requests. | the planned ADK agent; today `spike/cli.py` |
| **MCP client** | The connection library inside the host. Speaks MCP to one endpoint. | Python SDK `Client` |
| **MCP server** | A small service that exposes **tools** (and optionally resources and prompts) over MCP. | `mcp-alpha`, `mcp-beta` **[demo]** |
| **Tool** | A named function with a description and an input schema: `read_thing(key)`. The server runs it. | `read_thing`, `write_thing` **[demo]** |
| **MCP gateway** | A proxy in front of the servers. One URL for the agent; it authenticates, authorizes, routes, rate-limits and audits. | agentgateway **[demo]** |
| **IdP / authorization server** | Authenticates callers and **issues tokens**. Separate from the gateway and from the servers. | Keycloak **[planned]**; a throwaway key today |
| **JWT** | The token format: a signed JSON document. Anyone holding the IdP's public key can verify it offline. | `.spike/*.jwt` **[demo]** |

MCP itself is JSON-RPC 2.0 over a transport: `stdio` for local processes, **streamable HTTP** for remote servers.
The two calls that matter: `tools/list` ("what can I do?") and `tools/call` ("do this"). The 2026-07-28 revision made
the HTTP transport **stateless**: no session id, no initialize handshake, so any replica can answer any request
and servers sit behind a normal load balancer. **[demo]** (servers run `stateless_http=True`).

## 2. What a tool call looks like (the request flow)

An agent is asked "what pods are crashing?". Behind that one sentence:

```
 1  LLM        → "call k8s_ops.list_pods(namespace=prod)"          (the model only *asks*)
 2  Agent      → gateway: tools/call + Authorization: Bearer <JWT>
 3  Gateway    verify JWT: signature, issuer, audience, expiry     (401 if any fails)
 4  Gateway    authorize: may this caller use THIS tool?           (denied tools are hidden / refused)
 5  Gateway    rate-limit, guardrails, write audit line            (who, which tool, allowed/denied)
 6  Gateway    → server: tools/call, with a token for the server   (service-to-service auth)
 7  Server     verify that token, run the tool, call the real system with its OWN identity
 8  Server → Gateway → Agent: result  →  LLM reads it and answers
```
Steps 3, 4 and the 401/refusal behaviour are what `./scripts/test.sh` proves today (13 checks). **[demo]**
Steps 5 (audit) and 6 (gateway-to-server token) are **[planned]**.

## 3. OAuth and JWT, in the order they happen

The agent starts with **no token**. MCP's authorization spec (OAuth 2.1) defines how it finds out what to do:

```
 1  Agent   → Gateway : request, no token
 2  Gateway → Agent   : 401 + WWW-Authenticate: resource_metadata=<url>      (RFC 9728)
 3  Agent   → Gateway : GET that url  ("protected resource metadata")
 4  Gateway → Agent   : { resource, authorization_servers:[IdP], scopes_supported }
 5  Agent   → IdP     : discover IdP endpoints (RFC 8414), authenticate (auth-code + PKCE for a user,
                        client-credentials for a service agent), asking for audience = this gateway (RFC 8707)
 6  IdP     → Agent   : access token (a JWT)
 7  Agent   → Gateway : request + Authorization: Bearer <JWT>                 → flow in section 2
```
This is the second diagram you sent. Today **[demo]** the gateway only does step 7 and returns a bare 401 at
step 1/2; steps 2–6 need the gateway's MCP-auth block plus Keycloak **[planned]**.

**What is inside the JWT** (decode any file in `.spike/` at jwt.io, they are fake and expire):

| Claim | Meaning | Checked by |
|---|---|---|
| `iss` | who issued it (the IdP) | gateway: must equal the configured issuer |
| `aud` | who it is *for* (this gateway) | gateway: must contain its own name; stops a token for service X working on Y |
| `exp` / `nbf` | validity window | gateway |
| signature (`kid` in header) | proves the IdP signed it | gateway, using the IdP's public keys (JWKS) |
| `sub`, `role` / `scope` | who the caller is and what they may do | gateway's authorization rule (CEL) |

Why JWT: the gateway verifies it **without calling the IdP on every request** (just a cached public-key set), so it
adds almost no latency and does not depend on the IdP being up. The cost: a stolen token works until it expires, so
keep lifetimes short and scopes narrow.

## 4. Authentication vs authorization (do not blur them)

* **Authentication**: "who is calling?" → JWT validation. Fails with **401**. **[demo]** 5 test cases.
* **Authorization**: "may they do this?" → role/scope rule on the tool. Here it is a CEL expression on token claims:
  `jwt.role == "viewer" && mcp.tool.name.endsWith("read_thing")`. A viewer's `tools/list` simply **omits** tools they
  cannot use, and a direct call gets "Unknown tool". **[demo]** 8 test cases.
* **Downstream identity**: the *server* then acts on the real system as its own least-privilege service account, not
  as the caller. The caller's rights limit which tool runs; the server's rights limit what that tool can touch.

## 5. One gateway or many?

**The agent connects to one gateway URL; the gateway fans out to many servers.** That is the point of federation:
`tools/list` returns the merged catalogue, so adding a server means registering it, not reconfiguring every agent
**[demo]**. How many *gateways* you run is a security-boundary question, not a count-of-servers question. My guidance
(from the research, not a standard):

| Situation | Choice |
|---|---|
| One team/org, one trust level, internal tools | **One gateway.** One place for policy and audit. |
| Different environments (dev / staging / prod) | **One per environment.** Never let a dev agent route to prod tools. |
| Regulated or sensitive data (finance, PII) vs ordinary tools | **Separate gateway** with its own IdP audience, network and audit retention. Smaller blast radius. |
| External/partner agents calling in vs internal agents | **Separate edge gateway** for external (WAF, rate limits, stricter scopes); internal one behind it. |
| Many teams each owning servers | One shared gateway for discovery and policy, with **per-team route and policy objects** (or a gateway per team if they need independent release cycles). |
| A single MCP server | Still worth a gateway if you need central auth and audit; skip it for a purely local `stdio` tool. |

Anti-patterns: a gateway per MCP server (you have rebuilt "every server does its own auth", with extra hops); one
global gateway spanning prod and dev; a gateway that passes the caller's token straight through to servers.

## 6. Best practices (and where this repo stands)

| Practice | Why | Status |
|---|---|---|
| Servers are OAuth *resource servers*; a separate IdP issues tokens | Spec requirement; keeps secrets and login out of every server | IdP **[planned]** |
| Validate `aud`, `iss`, `exp`, signature on **every** request | Stops token reuse across services | **[demo]** |
| **No token passthrough**: gateway exchanges for a token scoped to the server | Spec forbids it; prevents the confused-deputy problem | **[planned]** |
| Least privilege per tool, default deny, hide what the caller cannot use | An agent can only misuse what it can see | **[demo]** |
| Short-lived tokens, narrow scopes, step-up for sensitive tools | Limits damage from a leaked token | **[planned]** |
| Each server runs as its own service account (Workload Identity, no keys) | Server compromise ≠ project compromise | **[planned]** |
| Network: only the gateway can reach servers (default-deny NetworkPolicy) | Auth is bypassed if servers are reachable directly | **[planned]** |
| Audit every decision (caller, tool, allow/deny) but never log argument values | Accountability without leaking secrets | gateway log has caller+status **[demo]**; tool-level audit **[planned]** |
| Rate limits per caller and tool | An agent in a loop is a denial-of-service on your own systems | **[planned]** |
| Treat tool *output* and tool *descriptions* as untrusted | Prompt injection and "tool poisoning": text from a tool can try to instruct the LLM | **[planned]** guardrails + description pinning |
| Stateless servers, health checks, 2+ replicas | Survives restarts; scales horizontally | stateless **[demo]** |
| TLS everywhere; no public plaintext listener | Tokens are bearer credentials | public HTTP today (known gap) |
| Human approval for destructive tools (writes/deletes) | LLMs make mistakes | **[planned]** (tickets server) |

## 7. See it yourself (about two minutes)

```bash
./scripts/pf.sh                                   # terminal 1
.venv/bin/python spike/cli.py list viewer         # authenticated as viewer: 2 read tools
.venv/bin/python spike/cli.py list operator       # authenticated as operator: 4 tools (same gateway, different view)
.venv/bin/python spike/cli.py list expired        # HTTP 401: authentication failed
.venv/bin/python spike/cli.py call viewer mcp-alpha-8000_write_thing key=a value=b   # authorization failed
kubectl -n agentgateway-system logs deploy/agentgateway-proxy --tail=5   # the audit trail: caller + status
```
Two roles, one URL, different tool lists: that is authentication and authorization at a gateway in one picture.
