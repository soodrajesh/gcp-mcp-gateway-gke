#!/usr/bin/env bash
# Build the platform end to end: GKE Autopilot -> agentgateway -> Keycloak (IdP) + MCP servers + policies.
# Idempotent. `--plan` shows the Terraform plan and stops. About 12 minutes from nothing, mostly the cluster.
source "$(dirname "$0")/lib.sh"
PLAN_ONLY=0; for a in "$@"; do [ "$a" = "--plan" ] && PLAN_ONLY=1; done
need gcloud; need terraform; need kubectl; need helm; need curl; need python3; need openssl
command -v gke-gcloud-auth-plugin >/dev/null || die "gke-gcloud-auth-plugin missing" "gcloud components install gke-gcloud-auth-plugin"
log "Project $PROJECT_ID · $REGION · cluster $CLUSTER · agentgateway $AGW_VERSION"

log "1/6 Python venv (MCP SDK + JWT tooling for the tests)"
[ -x "$PY" ] || python3 -m venv "$ROOT/.venv"
"$PY" -m pip install -q -r tests/requirements.txt -r servers/requirements-dev.txt && ok "mcp $("$PY" -m pip show mcp | awk '/^Version/{print $2}')"

log "2/6 Terraform: state bucket + Autopilot cluster (~8-10 min the first time)"
"$ROOT/scripts/bootstrap.sh" "$PROJECT_ID" "$REGION" >/dev/null && ok "gs://$STATE_BUCKET"
$TF init -input=false -backend-config="bucket=$STATE_BUCKET" >/dev/null
set_authorized_cidr; resolve_suffix
$TF plan -input=false -no-color -out=up.tfplan | grep -E "^\s+# .* (will be|must be)|^Plan:|^No changes" || true
[ "$PLAN_ONLY" = 1 ] && exit 0
$TF apply -input=false -no-color up.tfplan >/dev/null && rm -f terraform/up.tfplan && ok "cluster $CLUSTER ready"

log "3/6 Cluster credentials"
cluster_credentials; ok "$(kubectl config current-context)"
wait_for "API server reachable" 300 kubectl get --raw /healthz

log "4/6 agentgateway $AGW_VERSION (GKE already ships the Gateway API CRDs)"
helm upgrade -i --create-namespace -n agentgateway-system --version "$AGW_VERSION" agentgateway-crds oci://cr.agentgateway.dev/charts/agentgateway-crds >/dev/null
helm upgrade -i -n agentgateway-system --version "$AGW_VERSION" agentgateway oci://cr.agentgateway.dev/charts/agentgateway --wait --timeout 8m >/dev/null
ok "control plane running"

log "5/6 Server image, synthetic data, Keycloak, MCP servers, gateway (policies come after Keycloak is serving its signing keys)"
load_outputs
"$ROOT/scripts/build-images.sh"
"$ROOT/scripts/seed-finops.sh"
gen_secrets; ok "secrets in .secrets/ (gitignored)"
if ! helm status mcp-platform -n "$NS" >/dev/null 2>&1; then helm_deploy --set policies.enabled=false; fi
kubectl -n "$NS" rollout status deploy/keycloak --timeout=600s >/dev/null && ok "Keycloak ready (realm imported)"
for d in mcp-k8s mcp-finops mcp-tickets; do kubectl -n "$NS" rollout status deploy/$d --timeout=600s >/dev/null && ok "$d ready"; done
wait_for "Gateway programmed" 300 bash -c "[ \"\$(kubectl -n $NS get gateway mcp-gateway -o jsonpath='{.status.conditions[?(@.type==\"Programmed\")].status}')\" = True ]"

log "6/6 Policies: JWT + MCP discovery + role check, per-tool RBAC"
helm_deploy --set policies.enabled=true
for p in mcp-authn tool-rbac; do
  wait_for "policy $p accepted and attached" 120 bash -c "kubectl -n $NS get agentgatewaypolicy $p -o jsonpath='{.status.ancestors[0].conditions[*].status}' | grep -qv False"
done
ok "done. ./scripts/test.sh to verify, ./scripts/down.sh when finished"
