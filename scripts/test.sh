#!/usr/bin/env bash
# Live checks through the gateway (port-forwarded). Exit code is non-zero if any check fails.
source "$(dirname "$0")/lib.sh"
need kubectl; [ -x "$PY" ] || die "run ./scripts/up.sh first"; [ -d "$KEYS" ] || die "no .spike/ tokens: run ./scripts/up.sh"
cluster_credentials
kubectl -n agentgateway-system port-forward deploy/agentgateway-proxy "$PF_PORT:80" >/dev/null 2>&1 &
PF=$!; trap 'kill $PF 2>/dev/null || true' EXIT
wait_for "gateway reachable on localhost:$PF_PORT" 60 bash -c "curl -s -o /dev/null http://localhost:$PF_PORT/"
"$PY" spike/test_matrix.py "http://localhost:$PF_PORT/mcp" "$KEYS"
