#!/usr/bin/env bash
# Keep port-forwards to the gateway (localhost:18080) and Keycloak (localhost:18081) open for tests/cli.py. Ctrl-C to stop.
source "$(dirname "$0")/lib.sh"
cluster_credentials
start_port_forwards; trap 'kill $PF_PIDS 2>/dev/null || true' EXIT
echo "gateway  http://localhost:$GW_PORT/mcp   |   keycloak http://localhost:$KC_PORT   (Ctrl-C to stop)"
wait
