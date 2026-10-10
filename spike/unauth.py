import asyncio
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client

def leaves(e):
    return [x for s in e.exceptions for x in leaves(s)] if hasattr(e, "exceptions") else [e]

async def probe(label, token):
    seen = []
    async def hook(resp):
        seen.append((resp.request.method, resp.status_code, resp.headers.get("www-authenticate")))
    h = {"Authorization": f"Bearer {token}"} if token else {}
    http = create_mcp_http_client(headers=h)
    http.event_hooks["response"].append(hook)
    try:
        async with http:
            async with Client(streamable_http_client("http://localhost:18080/mcp", http_client=http)) as c:
                await c.list_tools()
    except BaseException as e:
        print(label, "leaf:", [f"{type(x).__name__}: {str(x)[:100]}" for x in leaves(e)])
    print(label, "http:", seen[:4])

async def main():
    await probe("no-token", None)
    await probe("garbage", "abcdefg")
asyncio.run(main())
