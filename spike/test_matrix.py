"""Live checks through the gateway. Every denial is paired with a positive case so a broken setup cannot pass."""
import asyncio, sys
from pathlib import Path

from mcp.client.client import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

URL, TOK = sys.argv[1], Path(sys.argv[2])
RESULTS: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}")


def token(name: str) -> str:
    return (TOK / f"{name}.jwt").read_text().strip()


async def session(tok: str | None, fn):
    """Run fn(client) with the given bearer token; return (result, [http statuses seen])."""
    statuses: list[int] = []

    async def hook(resp):
        statuses.append(resp.status_code)

    http = create_mcp_http_client(headers={"Authorization": f"Bearer {tok}"} if tok else {})
    http.event_hooks["response"].append(hook)
    try:
        async with http:
            async with Client(streamable_http_client(URL, http_client=http)) as c:
                return await fn(c), statuses
    except BaseException as e:  # noqa: BLE001 - we want the status, whatever the SDK raises
        return e, statuses


async def tools(c):
    return sorted(t.name for t in (await c.list_tools()).tools)


async def call(c, name, **args):
    """Return the tool's text, or the exception it raised (a refusal is a normal outcome we assert on)."""
    try:
        r = await c.call_tool(name, args)
        return r.content[0].text
    except Exception as e:  # noqa: BLE001
        return e


async def main() -> int:
    print("\n1. Authentication (every request must carry a valid token)")
    for label, tok in [("no token", None), ("garbage token", "abcdefg"), ("expired token", token("expired")),
                       ("wrong audience", token("wrong_audience")), ("signed by an untrusted key", token("wrong_signer"))]:
        res, st = await session(tok, tools)
        check(f"{label} -> rejected with HTTP 401", isinstance(res, BaseException) and 401 in st, f"statuses={st[:3]}")

    print("\n2. Viewer role (read tools only)")
    res, _ = await session(token("viewer"), tools)
    check("viewer can list tools", isinstance(res, list))
    names = res if isinstance(res, list) else []
    check("viewer sees both servers' read tools", {"mcp-alpha-8000_read_thing", "mcp-beta-8000_read_thing"} <= set(names), str(names))
    check("viewer sees NO write tools", not any("write" in n for n in names))
    for srv in ("alpha", "beta"):
        res, _ = await session(token("viewer"), lambda c, s=srv: call(c, f"mcp-{s}-8000_read_thing", key="k"))
        check(f"viewer can call {srv} read_thing", res == f"{srv}: read k", str(res)[:60])
    res, _ = await session(token("viewer"), lambda c: call(c, "mcp-alpha-8000_write_thing", key="k", value="v"))
    check("viewer calling write_thing is refused", isinstance(res, BaseException) and "Unknown tool" in str(res), str(res)[:70])

    print("\n3. Operator role (everything)")
    res, _ = await session(token("operator"), tools)
    check("operator sees all 4 tools (2 servers x read/write)", isinstance(res, list) and len(res) == 4, str(res))
    res, _ = await session(token("operator"), lambda c: call(c, "mcp-beta-8000_write_thing", key="k", value="v"))
    check("operator can call write_thing", res == "beta: wrote k=v", str(res)[:60])

    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
