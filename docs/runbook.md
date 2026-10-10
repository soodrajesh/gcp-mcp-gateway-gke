# Runbook

## Prerequisites
`gcloud` (logged in, project with billing), `terraform` >= 1.9, `kubectl`, `helm` >= 3.12, `python3`,
`gke-gcloud-auth-plugin` (`gcloud components install gke-gcloud-auth-plugin`).

## Bring it up / down
```bash
./scripts/up.sh --plan   # show what Terraform would create, change nothing
./scripts/up.sh          # build everything (~12 min). Safe to re-run.
./scripts/down.sh        # delete everything; prints clusters/forwarding-rules/addresses = 0 0 0
```
`up.sh` creates a throwaway signing key and four tokens in `.spike/` (viewer, operator, plus three bad ones).

## Test
```bash
./scripts/test.sh        # 13 checks; every denial is paired with a positive case
```

## Try it yourself
```bash
./scripts/pf.sh          # terminal 1: gateway on localhost:18080 (leave running)
# terminal 2:
.venv/bin/python spike/cli.py list viewer                                           # 2 read tools
.venv/bin/python spike/cli.py list operator                                         # 4 tools
.venv/bin/python spike/cli.py call viewer mcp-alpha-8000_read_thing key=hello       # works
.venv/bin/python spike/cli.py call viewer mcp-alpha-8000_write_thing key=a value=b  # refused: Unknown tool
.venv/bin/python spike/cli.py call operator mcp-alpha-8000_write_thing key=a value=b # works
.venv/bin/python spike/cli.py list expired                                          # 401
```

## Look inside the cluster
```bash
gcloud container clusters get-credentials mcp-gw-eu --region europe-west1
kubectl get pods -n agentgateway-system -n spike
kubectl get gateway,httproute,agentgatewaybackend,agentgatewaypolicy -A
kubectl -n agentgateway-system logs deploy/agentgateway-proxy --tail=20   # one line per request, with status
kubectl -n spike get agentgatewaypolicy tool-rbac -o yaml                  # the RBAC rule (CEL)
```

## Change who may call what
Edit the `matchExpressions` in `spike/policy.yaml.tpl` (CEL over `jwt.*` claims and `mcp.tool.name`), then
re-run `./scripts/up.sh`. Anything not matched by an Allow rule is denied.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `kubectl` says `gke-gcloud-auth-plugin not found` | The plugin is in the gcloud SDK bin dir, not on PATH. The scripts add it; for your shell: `export PATH="$(gcloud info --format='value(installation.sdk_root)')/bin:$PATH"` |
| `kubectl` times out after your IP changed | The API allows only the IP used at `up.sh` time. Re-run `./scripts/up.sh`. |
| Server pods not Ready for ~1 min | They `pip install` the MCP SDK at start. Normal. |
| `curl` JSON-RPC to `/mcp` returns 400 with an empty body | MCP 2026-07-28 request shape differs from the old one. Use `spike/cli.py` / the SDK client. |
| Test passes locally but you changed a policy and nothing changed | Check `kubectl get agentgatewaypolicy -A`: status must be `Accepted` and `Attached`. |
| A denied tool returns "Unknown tool", not 403 | By design: the gateway hides tools the caller may not use. In the gateway log it shows as `http.status=400` with the caller's `jwt.sub`. |

## Cost check
`gcloud container clusters list`, `gcloud compute forwarding-rules list`, `gcloud compute addresses list`
should all be empty after `down.sh`.
