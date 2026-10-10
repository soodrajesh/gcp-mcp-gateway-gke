#!/usr/bin/env bash
# Build the container images with Cloud Build, as the dedicated build service account. Tag = hash of the sources, so an
# unchanged tree is never rebuilt. Needs the Terraform outputs (run after the infrastructure step).
source "$(dirname "$0")/lib.sh"
need gcloud; load_outputs
build() { # <dir> <image>
  local dir="$1" image="$2"
  if gcloud artifacts docker images describe "$image" --project "$PROJECT_ID" >/dev/null 2>&1; then ok "image already built: ${image##*/}"; return; fi
  log "Cloud Build: ${image##*/}"
  gcloud builds submit "$ROOT/$dir" --config "$ROOT/$dir/cloudbuild.yaml" --substitutions "_IMAGE=$image" \
    --service-account "$($TF output -raw build_sa)" --gcs-source-staging-dir "gs://$($TF output -raw build_bucket)/src" \
    --region "$REGION" --project "$PROJECT_ID" --suppress-logs >/dev/null && ok "pushed ${image##*/}"
}
build servers "$SERVERS_IMAGE"
build agent "$AGENT_IMAGE"
