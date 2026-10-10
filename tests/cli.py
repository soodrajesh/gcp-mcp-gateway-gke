"""Poke the gateway by hand, as any of the Keycloak clients. Needs ./scripts/pf.sh running in another terminal.

    .venv/bin/python tests/cli.py token agent-viewer              # show the decoded token claims
    .venv/bin/python tests/cli.py list agent-viewer               # tools this caller may see
    .venv/bin/python tests/cli.py call agent-viewer mcp-alpha-8000_read_thing key=hello
    .venv/bin/python tests/cli.py call agent-viewer mcp-alpha-8000_write_thing key=a value=b    # refused
    .venv/bin/python tests/cli.py call agent-operator mcp-alpha-8000_write_thing key=a value=b  # allowed
    .venv/bin/python tests/cli.py list agent-noaud                # valid token, not for this gateway: 401
    .venv/bin/python tests/cli.py list agent-norole               # right audience, no role: 403
Clients: agent-viewer, agent-operator, agent-norole, agent-noaud.
"""
import asyncio, json, sys

from common import call, claims, get_token, leaf, list_tools, session

action, client, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
st, body = get_token(client)
if st != 200:
    print(f"IdP refused to issue a token for {client}: HTTP {st} {body}"); sys.exit(1)
token = body["access_token"]


async def main() -> int:
    if action == "token":
        print(json.dumps({k: v for k, v in claims(token).items() if k in ("iss", "aud", "roles", "azp", "scope", "exp")}, indent=2))
        return 0
    if action == "list":
        res, seen = await session(token, list_tools)
        if isinstance(res, BaseException):
            print(f"REFUSED as {client}: HTTP {seen[0][0] if seen else '?'} ({leaf(res)})"); return 1
        print("\n".join(res)); return 0
    res, seen = await session(token, lambda c: call(c, rest[0], **dict(a.split("=", 1) for a in rest[1:])))
    if isinstance(res, BaseException):
        print(f"REFUSED as {client}: {leaf(res)}" if not seen or seen[0][0] == 200 else f"REFUSED as {client}: HTTP {seen[0][0]}"); return 1
    print(res); return 0

sys.exit(asyncio.run(main()))
