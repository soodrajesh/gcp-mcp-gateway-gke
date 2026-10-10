#!/usr/bin/env bash
# Shared helpers for up.sh / test.sh / down.sh. Sourced, not executed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1${2:+ ($2)}"; }

# gke-gcloud-auth-plugin ships with gcloud but is often not on PATH
SDK_BIN="$(gcloud info --format='value(installation.sdk_root)' 2>/dev/null)/bin"
[ -d "$SDK_BIN" ] && export PATH="$SDK_BIN:$PATH"
export USE_GKE_GCLOUD_AUTH_PLUGIN=True

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-europe-west1}"
CLUSTER="${CLUSTER:-mcp-gw-eu}"
[ -n "$PROJECT_ID" ] || die "no project: 'export PROJECT_ID=...' or 'gcloud config set project'"

AGW_VERSION="${AGW_VERSION:-v1.6.0}"
TF="terraform -chdir=$ROOT/terraform"
STATE_BUCKET="${PROJECT_ID}-tfstate"
NS="${NS:-mcp-platform}"
SECRETS="$ROOT/.secrets"
GW_PORT="${GW_PORT:-18080}"
KC_PORT="${KC_PORT:-18081}"
PY="$ROOT/.venv/bin/python"

my_ip() { curl -fsS https://api.ipify.org 2>/dev/null || curl -fsS https://ifconfig.me 2>/dev/null; }

export TF_VAR_project_id="$PROJECT_ID" TF_VAR_region="$REGION" TF_VAR_cluster_name="$CLUSTER"
set_authorized_cidr() {
  local ip; ip="$(my_ip || true)"
  [ -n "$ip" ] || die "could not determine your public IP (needed to allow kubectl access)"
  export TF_VAR_authorized_cidr="$ip/32"
}

cluster_credentials() {
  gcloud container clusters get-credentials "$CLUSTER" --region "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1
}

wait_for() { # <description> <timeout-seconds> <command...>
  local desc="$1" timeout="$2"; shift 2
  local end=$(( $(date +%s) + timeout ))
  until "$@" >/dev/null 2>&1; do
    [ "$(date +%s)" -lt "$end" ] || die "timed out after ${timeout}s waiting for: $desc"
    sleep 5
  done
  ok "$desc"
}

# helm values for the chart: secrets are generated once and kept in .secrets/ (gitignored)
gen_secrets() {
  mkdir -p "$SECRETS"; chmod 700 "$SECRETS"
  for n in kc-admin agent-viewer agent-operator agent-norole agent-noaud; do
    [ -f "$SECRETS/$n" ] || ( umask 077; openssl rand -hex 16 > "$SECRETS/$n" )
  done
}
SUFFIX_FILE="$ROOT/.deploy-suffix"
# Firestore will not let a deleted database name be reused straight away, so each deployment gets its own suffix.
resolve_suffix() {
  [ -f "$SUFFIX_FILE" ] || printf -- '-%s' "$(date +%y%m%d%H%M)" > "$SUFFIX_FILE"
  export TF_VAR_suffix="$(cat "$SUFFIX_FILE")"
}
tf_out() { $TF output -raw "$1"; }
# Terraform outputs the chart needs
load_outputs() {
  GATEWAY_GSA="$(tf_out gateway_gsa)"; FINOPS_GSA="$(tf_out finops_gsa)"; TICKETS_GSA="$(tf_out tickets_gsa)"
  FIRESTORE_DB="$(tf_out firestore_database)"; IMAGE_REPO="$(tf_out image_repo)"
  IMAGE_TAG="sha-$(cd "$ROOT/servers" && find . -type f \( -name '*.py' -o -name Dockerfile -o -name requirements.txt \) -not -path './tests/*' | sort | xargs shasum | shasum | cut -c1-12)"
  IMAGE="$IMAGE_REPO/mcp-servers:$IMAGE_TAG"
}
helm_deploy() { # extra helm args...
  helm upgrade -i mcp-platform "$ROOT/deploy/chart" -n "$NS" --create-namespace \
    --set keycloak.adminPassword="$(cat "$SECRETS/kc-admin")" \
    --set keycloak.clients.agent-viewer.secret="$(cat "$SECRETS/agent-viewer")" \
    --set keycloak.clients.agent-operator.secret="$(cat "$SECRETS/agent-operator")" \
    --set keycloak.clients.agent-norole.secret="$(cat "$SECRETS/agent-norole")" \
    --set keycloak.clients.agent-noaud.secret="$(cat "$SECRETS/agent-noaud")" \
    --set gatewayGsa="${GATEWAY_GSA:?run load_outputs first}" --set project="$PROJECT_ID" --set image="$IMAGE" \
    --set servers.finops.gsa="$FINOPS_GSA" --set servers.tickets.gsa="$TICKETS_GSA" \
    --set servers.tickets.env.FIRESTORE_DATABASE="$FIRESTORE_DB" "$@" >/dev/null
}
# port-forward the gateway and Keycloak to localhost (the canonical URLs in the chart); sets PF_PIDS
start_port_forwards() {
  kubectl -n "$NS" port-forward svc/mcp-gateway "$GW_PORT:80" >/dev/null 2>&1 & PF_PIDS="$!"
  kubectl -n "$NS" port-forward svc/keycloak "$KC_PORT:8080" >/dev/null 2>&1 & PF_PIDS="$PF_PIDS $!"
  wait_for "gateway reachable on localhost:$GW_PORT" 60 bash -c "curl -s -o /dev/null http://localhost:$GW_PORT/.well-known/oauth-protected-resource/mcp"
  wait_for "Keycloak reachable on localhost:$KC_PORT" 60 bash -c "curl -sf -o /dev/null http://localhost:$KC_PORT/realms/mcp"
}
