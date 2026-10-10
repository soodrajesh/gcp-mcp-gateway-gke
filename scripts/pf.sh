#!/usr/bin/env bash
# Keep a port-forward to the gateway open on localhost:18080 so you can use spike/cli.py. Ctrl-C to stop.
source "$(dirname "$0")/lib.sh"
cluster_credentials
echo "gateway on http://localhost:$PF_PORT/mcp  (Ctrl-C to stop)"
exec kubectl -n agentgateway-system port-forward deploy/agentgateway-proxy "$PF_PORT:80"
