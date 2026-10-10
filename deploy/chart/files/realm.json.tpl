{
  "realm": "{{ .Values.realm }}",
  "enabled": true,
  "sslRequired": "none",
  "accessTokenLifespan": 300,
  "roles": {"realm": [{"name": "mcp-viewer"}, {"name": "mcp-operator"}]},
  "components": {"org.keycloak.keys.KeyProvider": [
    {"name": "hmac-generated-hs512", "providerId": "hmac-generated", "subComponents": {}, "config": {"priority": ["100"], "algorithm": ["HS512"]}},
    {"name": "aes-generated", "providerId": "aes-generated", "subComponents": {}, "config": {"priority": ["100"]}},
    {
    "name": "mcp-fixed-signing-key", "providerId": "rsa", "subComponents": {},
    "config": {"priority": ["1000"], "enabled": ["true"], "active": ["true"], "algorithm": ["RS256"],
               "privateKey": [{{ required "keycloak.signingKey.privateKey is required" .Values.keycloak.signingKey.privateKey | toJson }}],
               "certificate": [{{ required "keycloak.signingKey.certificate is required" .Values.keycloak.signingKey.certificate | toJson }}]}
  }]},
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
