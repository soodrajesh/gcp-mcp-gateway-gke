#!/usr/bin/env bash
# Delete everything the spike created. Shows the destroy plan, applies that plan, then proves nothing is left.
source "$(dirname "$0")/lib.sh"
need gcloud; need terraform; need kubectl
if gcloud container clusters describe "$CLUSTER" --region "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  cluster_credentials
  log "Delete workloads first (releases the load balancer)"
  kubectl delete ns spike agentgateway-system --wait=true --timeout=300s 2>&1 | tail -2 || true
fi
$TF init -input=false -backend-config="bucket=$STATE_BUCKET" >/dev/null
set_authorized_cidr
log "Destroy plan"
$TF plan -destroy -input=false -no-color -out=down.tfplan | grep -E "will be destroyed|^Plan:" || true
$TF apply -input=false -no-color down.tfplan | tail -2; rm -f terraform/down.tfplan
log "Verify nothing billable is left"
c="$(gcloud container clusters list --project "$PROJECT_ID" --format='value(name)' | grep -c . || true)"
f="$(gcloud compute forwarding-rules list --project "$PROJECT_ID" --format='value(name)' | grep -c . || true)"
a="$(gcloud compute addresses list --project "$PROJECT_ID" --format='value(name)' | grep -c . || true)"
echo "clusters=$c forwarding-rules=$f reserved-addresses=$a"
[ "$c$f$a" = "000" ] && ok "clean" || warn "something is still there: check the counts above"
