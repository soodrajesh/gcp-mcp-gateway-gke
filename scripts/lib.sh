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
PF_PORT="${PF_PORT:-18080}"
KEYS="$ROOT/.spike"
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
