"""Does the platform survive its own restarts? Disruptive (kills the IdP and the gateway pod), so it runs last.
The point: Keycloak's signing key is pinned, so a restart does not make every cached key stale."""
import asyncio, base64, json, subprocess, sys, time

from common import ToolFailed, claims, get_token, list_tools, session

NS = "mcp-platform"
RESULTS: list[bool] = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}")


def sh(cmd, timeout=400):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def kid(token):
    h = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(h + "=" * (-len(h) % 4)))["kid"]


def replace_pod(label, timeout=420):
    """Delete the pod behind `label` and wait until a DIFFERENT pod is Ready."""
    old = sh(f"kubectl -n {NS} get pod -l {label} -o jsonpath='{{.items[0].metadata.name}}'").stdout.strip()
    sh(f"kubectl -n {NS} delete pod {old} --wait=false")
    end = time.time() + timeout
    while time.time() < end:
        r = sh(f"kubectl -n {NS} get pod -l {label} -o json")
        try:
            for p in json.loads(r.stdout)["items"]:
                ready = any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", []))
                if p["metadata"]["name"] != old and ready and "deletionTimestamp" not in p["metadata"]:
                    return old, p["metadata"]["name"]
        except (ValueError, KeyError):
            pass
        time.sleep(5)
    return old, None


def forward(svc, port, target):
    p = subprocess.Popen(f"exec kubectl -n {NS} port-forward svc/{svc} {port}:{target}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    return p


async def main() -> int:
    print("\nJ. Resilience: restart the IdP and the gateway; nothing may need re-configuring")
    st, body = get_token("agent-viewer")
    before = body["access_token"]
    old, new = replace_pod("app=keycloak")
    check("Keycloak was restarted (new pod Ready)", bool(new), f"{old} -> {new}")
    kc = forward("keycloak", 18082, 8080)          # our own forward: the harness's one died with the old pod
    KC = "http://localhost:18082"
    gw = None
    try:
        st, body = get_token("agent-viewer", base=KC)
        after = body.get("access_token", "") if st == 200 else ""
        check("the realm came back (a token is issued again)", bool(after), f"status={st}")
        check("the restarted IdP signs with the SAME key id", bool(after) and kid(after) == kid(before), f"{kid(before)[:10]} == {kid(after)[:10] if after else None}")
        res, seen = await session(after, list_tools)
        check("the gateway accepts the new token with NO policy change", isinstance(res, list) and len(res) == 7, f"status={seen[0][0] if seen else None}")
        res, _ = await session(before, list_tools)
        check("a token issued BEFORE the restart is still valid", isinstance(res, list) and len(res) == 7)

        old, new = replace_pod("gateway.networking.k8s.io/gateway-name=mcp-gateway")
        check("the gateway pod was replaced (new pod Ready)", bool(new), f"{old} -> {new}")
        gw = forward("mcp-gateway", 18083, 80)
        GW = "http://localhost:18083/mcp"
        st, body = get_token("agent-operator", base=KC)
        res, seen = await session(body["access_token"], list_tools, url=GW)
        check("the new gateway pod enforces the same policy (operator sees all 9 tools)", isinstance(res, list) and len(res) == 9, f"{len(res) if isinstance(res, list) else res}")
        st, body = get_token("agent-viewer", base=KC)
        res, _ = await session(body["access_token"], list_tools, url=GW)
        check("... and a viewer still sees only the 7 read tools", isinstance(res, list) and len(res) == 7)
        res, seen = await session("abcdefg", list_tools, url=GW)
        check("... and still refuses a garbage token (401)", bool(seen) and seen[0][0] == 401)
    finally:
        for p in (kc, gw):
            if p:
                p.terminate(); p.wait(timeout=10)

    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
