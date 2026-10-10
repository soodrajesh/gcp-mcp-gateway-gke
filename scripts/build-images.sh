#!/usr/bin/env bash
# Build the MCP server image with Cloud Build, as the dedicated build service account. Tag = hash of the sources, so an
# unchanged tree is never rebuilt. Needs the Terraform outputs (run after the infrastructure step).
source "$(dirname "$0")/lib.sh"
need gcloud; load_outputs
if gcloud artifacts docker images describe "$IMAGE" --project "$PROJECT_ID" >/dev/null 2>&1; then
  ok "image already built: $IMAGE_TAG"; exit 0
fi
log "Cloud Build: $IMAGE_TAG"
gcloud builds submit "$ROOT/servers" --config "$ROOT/servers/cloudbuild.yaml" --substitutions "_IMAGE=$IMAGE" \
  --service-account "$($TF output -raw build_sa)" --gcs-source-staging-dir "gs://$($TF output -raw build_bucket)/src" \
  --region "$REGION" --project "$PROJECT_ID" --suppress-logs >/dev/null && ok "pushed $IMAGE"
