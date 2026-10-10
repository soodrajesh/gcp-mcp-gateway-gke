import asyncio, sys
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client

URL = "http://localhost:18080/mcp"

async def run(label, token):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        http = create_mcp_http_client(headers=headers)
        async with streamable_http_client(URL, http_client=http) as streams:
            pass
    except Exception:
        pass
    try:
        http = create_mcp_http_client(headers=headers)
        async with http:
            tr = streamable_http_client(URL, http_client=http)
            async with Client(tr) as c:
                names = sorted(t.name for t in (await c.list_tools()).tools)
                print(f"[{label}] list_tools -> {names}")
                for n in ("mcp-alpha-8000_read_thing", "mcp-alpha-8000_write_thing"):
                    try:
                        args = {"key": "k"} if "read" in n else {"key": "k", "value": "v"}
                        r = await c.call_tool(n, args)
                        print(f"[{label}] call {n} -> {'ERR ' if r.is_error else 'OK '}{r.content[0].text}")
                    except Exception as e:
                        print(f"[{label}] call {n} -> DENIED {type(e).__name__}: {str(e)[:90]}")
    except Exception as e:
        print(f"[{label}] connect failed -> {type(e).__name__}: {str(e)[:140]}")

async def main():
    tok = lambda n: open(f"{n}.jwt").read()
    await run("no-token", None)
    await run("garbage", "abcdefg")
    await run("viewer", tok("viewer"))
    await run("operator", tok("operator"))
asyncio.run(main())
