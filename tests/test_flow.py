"""Live checks of the whole flow, in the order of the request-flow diagram. Every denial is paired with a
positive case, so a broken setup cannot pass by rejecting everything."""
import asyncio, sys

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

import uuid

from common import (GATEWAY, MCP_URL, REALM, ToolFailed, call, claims, get_token, http_get, leaf, list_tools, session)

RESULTS: list[bool] = []
RESOURCE = MCP_URL


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}")


def tok(client: str) -> str:
    st, body = get_token(client)
    assert st == 200, f"token for {client}: HTTP {st} {body}"
    return body["access_token"]


async def main() -> int:
    print("\nA. Discovery (diagram steps 1-4: no token, then find out where to authenticate)")
    res, seen = await session(None, list_tools)
    st, www = (seen[0] if seen else (None, None))
    check("1  request without a token -> HTTP 401", st == 401, f"status={st}")
    check("2  401 carries WWW-Authenticate pointing at the resource metadata",
          bool(www) and "resource_metadata" in www and "/.well-known/oauth-protected-resource" in www, str(www)[:110])
    st, prm = http_get(f"{GATEWAY}/.well-known/oauth-protected-resource/mcp")
    check("3  protected-resource metadata names this gateway as the resource", st == 200 and prm.get("resource") == RESOURCE,
          f"resource={prm.get('resource')}")
    check("   ... and advertises the scope clients should request", "mcp-gateway" in prm.get("scopes_supported", []))
    st, asm = http_get(f"{GATEWAY}/.well-known/oauth-authorization-server/mcp")
    check("4  authorization-server metadata (via the gateway) points at the IdP's token endpoint",
          st == 200 and asm.get("token_endpoint", "").endswith(f"/realms/{REALM}/protocol/openid-connect/token"),
          asm.get("token_endpoint", "")[-60:])

    print("\nB. Getting a token (diagram steps 5-6: authenticate at the IdP)")
    st, body = get_token("agent-viewer", "not-the-secret")
    check("wrong client secret -> the IdP refuses (HTTP 401, no token issued)", st == 401 and "access_token" not in body, f"status={st}")
    viewer, operator = tok("agent-viewer"), tok("agent-operator")
    cv = claims(viewer)
    check("viewer token: issuer is the IdP", cv["iss"].endswith(f"/realms/{REALM}"), cv["iss"])
    check("viewer token: audience is this gateway", RESOURCE in cv["aud"], str(cv["aud"]))
    check("viewer token: role claim is mcp-viewer only", cv.get("roles") == ["mcp-viewer"], str(cv.get("roles")))
    check("operator token: role claim is mcp-operator", claims(operator).get("roles") == ["mcp-operator"])

    print("\nC. Gateway authentication (step 7: every request carries a token the gateway can verify)")
    forged = jwt.encode({**claims(operator)}, rsa.generate_private_key(public_exponent=65537, key_size=2048),
                        algorithm="RS256", headers={"kid": jwt.get_unverified_header(operator)["kid"]})
    head, payload, sig = operator.split(".")
    tampered = f"{head}.{payload}.{sig[:-4]}{'AAAA' if not sig.endswith('AAAA') else 'BBBB'}"
    for label, t in [("garbage token", "abcdefg"),
                     ("operator's claims signed with a key the gateway does not trust", forged),
                     ("real operator token with a tampered signature", tampered),
                     ("valid Keycloak token that is not addressed to this gateway (no audience)", tok("agent-noaud"))]:
        res, seen = await session(t, list_tools)
        check(f"{label} -> HTTP 401", bool(seen) and seen[0][0] == 401, f"status={seen[0][0] if seen else None}")
    res, seen = await session(tok("agent-norole"), list_tools)
    check("valid token with the right audience but NO role -> denied (not 200)", bool(seen) and seen[0][0] in (401, 403), f"status={seen[0][0] if seen else None}")
    res, _ = await session(viewer, list_tools)
    check("positive control: the viewer's real token is accepted", isinstance(res, list))

    print("\nD. Per-tool authorization (viewer: read-only allow-list; operator: everything)")
    READ = {"k8s_list_pods", "k8s_get_pod_events", "k8s_get_pod_logs", "finops_cost_by_service", "finops_top_cost_skus",
            "tickets_list_tickets", "tickets_get_ticket"}
    WRITE = {"tickets_create_ticket", "tickets_update_ticket_status"}
    names = set(res) if isinstance(res, list) else set()
    check("viewer sees exactly the 7 read tools of the 3 servers", names == READ, str(sorted(names ^ READ)))
    check("viewer sees NO write tools", not (names & WRITE))
    r, _ = await session(operator, list_tools)
    check("operator sees all 9 tools", isinstance(r, list) and set(r) == READ | WRITE, str(r))
    for w, args in (("tickets_create_ticket", {"title": "x"}), ("tickets_update_ticket_status", {"ticket_id": "x", "status": "closed"})):
        r, _ = await session(viewer, lambda c, w=w, args=args: call(c, w, **args))
        check(f"viewer calling {w} is refused", isinstance(r, BaseException) and "Unknown tool" in str(leaf(r)), str(leaf(r))[:50])

    print("\nE. The three real servers, through the gateway, with real data")
    r, _ = await session(viewer, lambda c: call(c, "k8s_list_pods", namespace="mcp-platform"))
    pods = [p["name"] for p in r] if isinstance(r, list) else []
    check("k8s: lists the platform's pods (keycloak, gateway, 3 servers)",
          all(any(p.startswith(x) for p in pods) for x in ("keycloak", "mcp-gateway", "mcp-k8s", "mcp-finops", "mcp-tickets")), str(pods)[:90])
    r, _ = await session(viewer, lambda c: call(c, "k8s_list_pods", namespace="kube-system"))
    check("k8s: a namespace outside the allow-list is refused by the server", isinstance(r, ToolFailed), str(r)[:60])
    r, _ = await session(viewer, lambda c: call(c, "finops_cost_by_service", days=30))
    check("finops: BigQuery returns cost per service, highest first",
          isinstance(r, list) and len(r) >= 6 and r[0]["cost_usd"] >= r[-1]["cost_usd"] > 0, str(r[:2]) if isinstance(r, list) else str(r))
    r, _ = await session(viewer, lambda c: call(c, "finops_top_cost_skus", days=7, limit=3))
    check("finops: top SKUs honours the limit", isinstance(r, list) and len(r) == 3, str(r)[:80])
    title = f"live test {uuid.uuid4().hex[:8]}"
    r, _ = await session(operator, lambda c: call(c, "tickets_create_ticket", title=title, description="created by test_flow", priority="high"))
    tid = r.get("id") if isinstance(r, dict) else None
    check("tickets: operator creates a ticket (written to Firestore)", bool(tid) and r.get("status") == "open", str(r)[:70])
    r, _ = await session(viewer, lambda c: call(c, "tickets_get_ticket", ticket_id=tid or "none"))
    check("tickets: viewer reads it back", isinstance(r, dict) and r.get("title") == title, str(r)[:60])
    r, _ = await session(operator, lambda c: call(c, "tickets_create_ticket", title="bad", priority="urgent"))
    check("tickets: invalid input is rejected by the server", isinstance(r, ToolFailed), str(r)[:60])
    r, _ = await session(operator, lambda c: call(c, "tickets_update_ticket_status", ticket_id=tid or "none", status="closed"))
    check("tickets: operator closes it", isinstance(r, dict) and r.get("status") == "closed", str(r)[:60])
    r, _ = await session(viewer, lambda c: call(c, "tickets_list_tickets", status="closed"))
    check("tickets: it now shows in the closed list", isinstance(r, list) and any(t["id"] == tid for t in r))

    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
