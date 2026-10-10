#!/usr/bin/env bash
# Build the spike end to end: GKE Autopilot cluster -> agentgateway -> two MCP servers -> JWT + per-tool RBAC.
# Idempotent. `--plan` shows the Terraform plan and stops. About 12 minutes, mostly the cluster.
source "$(dirname "$0")/lib.sh"
PLAN_ONLY=0; for a in "$@"; do [ "$a" = "--plan" ] && PLAN_ONLY=1; done
need gcloud; need terraform; need kubectl; need helm; need curl; need python3
command -v gke-gcloud-auth-plugin >/dev/null || die "gke-gcloud-auth-plugin missing" "gcloud components install gke-gcloud-auth-plugin"
log "Project $PROJECT_ID · $REGION · cluster $CLUSTER · agentgateway $AGW_VERSION"

log "1/6 Python venv (MCP SDK + JWT tooling for the test client)"
[ -x "$PY" ] || python3 -m venv "$ROOT/.venv"
"$PY" -m pip install -q -r spike/requirements.txt && ok "mcp $("$PY" -m pip show mcp | awk '/^Version/{print $2}')"

log "2/6 Terraform: state bucket + Autopilot cluster (~8-10 min)"
"$ROOT/scripts/bootstrap.sh" "$PROJECT_ID" "$REGION" >/dev/null && ok "gs://$STATE_BUCKET"
$TF init -input=false -backend-config="bucket=$STATE_BUCKET" >/dev/null
set_authorized_cidr
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

log "5/6 MCP servers (alpha, beta) behind one federated backend"
kubectl apply -f spike/servers.yaml >/dev/null
kubectl -n spike create configmap server-src --from-file=spike/server.py --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -f spike/gateway.yaml >/dev/null
for d in mcp-alpha mcp-beta; do kubectl -n spike rollout status deploy/$d --timeout=600s >/dev/null && ok "$d ready"; done
wait_for "Gateway programmed" 300 bash -c "[ \"\$(kubectl -n agentgateway-system get gateway agentgateway-proxy -o jsonpath='{.status.conditions[?(@.type==\"Programmed\")].status}')\" = True ]"

log "6/6 Throwaway signing key + JWT/RBAC policy"
"$PY" spike/make_tokens.py "$KEYS" && ok "tokens in .spike/ (gitignored)"
"$PY" - <<'PY' | kubectl apply -f - >/dev/null
import pathlib
print(pathlib.Path("spike/policy.yaml.tpl").read_text().replace("__JWKS__", pathlib.Path(".spike/jwks.json").read_text()))
PY
wait_for "policies accepted" 120 bash -c "kubectl -n spike get agentgatewaypolicy tool-rbac -o jsonpath='{.status.ancestors[0].conditions[0].status}' | grep -q True"
ok "done. Run ./scripts/test.sh, then ./scripts/down.sh when finished"
