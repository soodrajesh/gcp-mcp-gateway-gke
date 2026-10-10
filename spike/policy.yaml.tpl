# Gateway-wide JWT validation + per-tool RBAC. __JWKS__ is filled in by scripts/up.sh.
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata: {name: jwt, namespace: agentgateway-system}
spec:
  targetRefs: [{group: gateway.networking.k8s.io, kind: Gateway, name: agentgateway-proxy}]
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
      - issuer: spike.local
        audiences: [mcp-gateway]
        jwks:
          inline: '__JWKS__'
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata: {name: tool-rbac, namespace: spike}
spec:
  targetRefs: [{group: agentgateway.dev, kind: AgentgatewayBackend, name: mcp}]
  backend:
    mcp:
      authorization:
        action: Allow
        policy:
          matchExpressions:
          - 'jwt.role == "operator"'
          - 'jwt.role == "viewer" && mcp.tool.name.endsWith("read_thing")'
