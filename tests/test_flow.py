"""Live checks of the whole flow, in the order of the request-flow diagram. Every denial is paired with a
positive case, so a broken setup cannot pass by rejecting everything."""
import asyncio, sys

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from common import (GATEWAY, KEYCLOAK, MCP_URL, REALM, call, claims, get_token, http_get, leaf, list_tools, session)

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

    print("\nD. Per-tool authorization (viewer: read only; operator: everything)")
    names = res if isinstance(res, list) else []
    check("viewer sees both servers' read tools", {"mcp-alpha-8000_read_thing", "mcp-beta-8000_read_thing"} <= set(names), str(names))
    check("viewer sees NO write tools", not any("write" in n for n in names))
    for srv in ("alpha", "beta"):
        r, _ = await session(viewer, lambda c, s=srv: call(c, f"mcp-{s}-8000_read_thing", key="k"))
        check(f"viewer can call {srv} read_thing", r == f"{srv}: read k", str(r)[:50])
    r, _ = await session(viewer, lambda c: call(c, "mcp-alpha-8000_write_thing", key="k", value="v"))
    check("viewer calling write_thing is refused", isinstance(r, BaseException) and "Unknown tool" in str(leaf(r)), str(leaf(r))[:60])
    r, _ = await session(operator, list_tools)
    check("operator sees all 4 tools", isinstance(r, list) and len(r) == 4, str(r))
    r, _ = await session(operator, lambda c: call(c, "mcp-beta-8000_write_thing", key="k", value="v"))
    check("operator can call write_thing", r == "beta: wrote k=v", str(r)[:50])

    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
