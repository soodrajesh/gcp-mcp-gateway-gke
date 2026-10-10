#!/usr/bin/env bash
# Run the AI agent once, as a Kubernetes Job, and print its transcript.
#   ./scripts/ask.sh viewer   "How many pods are running, and which cloud service costs the most?"
#   ./scripts/ask.sh operator "Open a ticket titled 'Investigate Keycloak restarts' with high priority"
# The SAME agent code runs in both cases; what it can do is decided by the token it gets (viewer: read tools; operator: all).
source "$(dirname "$0")/lib.sh"
ROLE="${1:?usage: ask.sh viewer|operator \"question\"}"; Q="${2:?usage: ask.sh viewer|operator \"question\"}"
case "$ROLE" in viewer|operator) ;; *) die "role must be viewer or operator";; esac
need kubectl; cluster_credentials; load_outputs
JOB="agent-$ROLE-$(date +%H%M%S)-$RANDOM"
kubectl -n "$NS" create -f - >/dev/null <<YAML
apiVersion: batch/v1
kind: Job
metadata: {name: $JOB, namespace: $NS, labels: {app: mcp-agent}}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 900
  template:
    metadata:
      labels: {app: mcp-agent, mcp-client: "true"}
    spec:
      restartPolicy: Never
      serviceAccountName: mcp-agent
      securityContext: {runAsNonRoot: true, runAsUser: 10001, seccompProfile: {type: RuntimeDefault}}
      containers:
      - name: agent
        image: $AGENT_IMAGE
        envFrom: [{configMapRef: {name: agent-config}}]
        env:
        - {name: CLIENT_ID, value: agent-$ROLE}
        - name: CLIENT_SECRET
          valueFrom: {secretKeyRef: {name: agent-credentials, key: $ROLE}}
        - name: QUESTION
          value: $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$Q")
        resources: {requests: {cpu: 250m, memory: 512Mi}}
        securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: [ALL]}}
YAML
for _ in $(seq 1 90); do
  st="$(kubectl -n "$NS" get job "$JOB" -o jsonpath='{.status.succeeded}{.status.failed}' 2>/dev/null)"
  [ -n "$st" ] && break; sleep 3
done
RAW="${AGENT_RAW:-$(mktemp)}"
kubectl -n "$NS" logs "job/$JOB" > "$RAW" 2>&1 || true
if grep -q '"event"' "$RAW"; then "$PY" "$ROOT/tests/transcript.py" < "$RAW"; else cat "$RAW"; die "agent produced no events (see above)"; fi
[ -n "${AGENT_RAW:-}" ] || rm -f "$RAW"
