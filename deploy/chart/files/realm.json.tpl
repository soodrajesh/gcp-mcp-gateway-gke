{
  "realm": "{{ .Values.realm }}",
  "enabled": true,
  "sslRequired": "none",
  "accessTokenLifespan": 300,
  "roles": {"realm": [{"name": "mcp-viewer"}, {"name": "mcp-operator"}]},
  "clientScopes": [{
    "name": "mcp-gateway", "protocol": "openid-connect",
    "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"},
    "protocolMappers": [
      {"name": "aud-gateway", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
       "config": {"included.custom.audience": "{{ .Values.resource }}", "access.token.claim": "true", "id.token.claim": "false"}},
      {"name": "aud-gateway-cluster", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
       "config": {"included.custom.audience": "{{ .Values.clusterResource }}", "access.token.claim": "true", "id.token.claim": "false"}},
      {"name": "roles", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-realm-role-mapper",
       "config": {"claim.name": "roles", "multivalued": "true", "jsonType.label": "String",
                  "access.token.claim": "true", "id.token.claim": "false", "userinfo.token.claim": "false"}}
    ]
  }],
  "clients": [
{{- $first := true }}
{{- range $name, $c := .Values.keycloak.clients }}
{{- if not $first }},{{ end }}{{ $first = false }}
    {"clientId": "{{ $name }}", "enabled": true, "protocol": "openid-connect", "publicClient": false,
     "clientAuthenticatorType": "client-secret", "secret": "{{ required (printf "keycloak.clients.%s.secret is required" $name) $c.secret }}",
     "serviceAccountsEnabled": true, "standardFlowEnabled": false, "directAccessGrantsEnabled": false,
     "defaultClientScopes": [{{ if $c.audience }}"mcp-gateway"{{ end }}], "optionalClientScopes": []}
{{- end }}
  ],
  "users": [
{{- $first = true }}
{{- range $name, $c := .Values.keycloak.clients }}
{{- if not $first }},{{ end }}{{ $first = false }}
    {"username": "service-account-{{ $name }}", "enabled": true, "serviceAccountClientId": "{{ $name }}",
     "realmRoles": [{{ if $c.role }}"{{ $c.role }}"{{ end }}]}
{{- end }}
  ]
}
