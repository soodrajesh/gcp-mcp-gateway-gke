#!/usr/bin/env bash
# Render docs/evidence/*.txt into terminal-style PNGs. test-output.txt (the full ./scripts/test.sh output) is split by
# section first. Needs: pip install playwright, Google Chrome.   usage: PYTHON=/path/to/python docs/diagrams/render_evidence.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"; EV=docs/evidence; SHOT=docs/diagrams/termshot.py
"$PY" - <<'PYEOF'
import re
t = open("docs/evidence/test-output.txt").read()
def part(start, end):
    i = t.index(start); j = t.index(end) if end else len(t)
    return t[i:j].rstrip() + "\n"
open("docs/evidence/test-output-flow.txt", "w").write(t[:t.index("F. Each server refuses")].rstrip() + "\n")
open("docs/evidence/test-output-isolation.txt", "w").write(part("F. Each server refuses", "I. The AI agent"))
open("docs/evidence/test-output-agent.txt", "w").write(part("I. The AI agent", "J. Resilience"))
open("docs/evidence/test-output-resilience.txt", "w").write(part("J. Resilience", None))
PYEOF
shot() { "$PY" "$SHOT" "$2" "$EV/$1.txt" "$EV/$1.png" >/dev/null; }
shot test-output-flow       "scripts/test.sh  (1/4: discovery, tokens, gateway auth, per-tool RBAC, real servers)"
shot test-output-isolation  "scripts/test.sh  (2/4: servers refuse non-gateway callers, NetworkPolicy, egress)"
shot test-output-agent      "scripts/test.sh  (3/4: the AI agent through the gateway, incl. prompt injection)"
shot test-output-resilience "scripts/test.sh  (4/4: restart the IdP and the gateway; nothing to re-configure)"
shot cli-session            "tests/cli.py  (by hand, with the real tools)"
shot agent-facts            "AI agent (viewer identity): a question that needs two servers"
shot agent-injection        "AI agent (viewer identity) reading a ticket that carries a prompt-injection payload"
shot agent-write            "AI agent (operator identity): same code, a token that may write"
shot server-audit           "server-side audit log (caller = the gateway's identity)"
shot gateway-log            "gateway access log (caller = JWT sub)"
shot cluster                "what is running in the cluster"
shot iam                    "least-privilege, keyless identities"
echo "rendered PNGs in $EV"
