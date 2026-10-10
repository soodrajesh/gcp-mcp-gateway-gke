#!/usr/bin/env bash
# Live checks of the whole flow (discovery -> token -> gateway -> per-tool RBAC). Non-zero exit if any check fails.
source "$(dirname "$0")/lib.sh"
need kubectl; [ -x "$PY" ] || die "run ./scripts/up.sh first"; [ -d "$SECRETS" ] || die "no .secrets/: run ./scripts/up.sh"
cluster_credentials
start_port_forwards; trap 'kill $PF_PIDS 2>/dev/null || true' EXIT
"$PY" "$ROOT/tests/test_flow.py"
