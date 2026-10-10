"""Poke the gateway by hand.

    .venv/bin/python spike/cli.py list viewer
    .venv/bin/python spike/cli.py call viewer mcp-alpha-8000_read_thing key=hello
    .venv/bin/python spike/cli.py call viewer mcp-alpha-8000_write_thing key=a value=b   # refused
    .venv/bin/python spike/cli.py call operator mcp-alpha-8000_write_thing key=a value=b # allowed

Roles: viewer, operator (or expired, wrong_audience, wrong_signer to see the 401s). Needs scripts/pf.sh running.
"""
import asyncio, os, sys
from pathlib import Path

from mcp.client.client import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

URL = os.environ.get("MCP_URL", "http://localhost:18080/mcp")
role, action, rest = sys.argv[2] if len(sys.argv) > 2 else "viewer", sys.argv[1], sys.argv[3:]
token = (Path(__file__).resolve().parent.parent / ".spike" / f"{role}.jwt").read_text().strip()


def leaf(e):
    return leaf(e.exceptions[0]) if hasattr(e, "exceptions") else e


async def main():
    seen = []

    async def hook(resp):
        seen.append(resp.status_code)

    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    http.event_hooks["response"].append(hook)
    try:
        async with http, Client(streamable_http_client(URL, http_client=http)) as c:
            if action == "list":
                for t in (await c.list_tools()).tools:
                    print(t.name)
            else:
                try:
                    r = await c.call_tool(rest[0], dict(a.split("=", 1) for a in rest[1:]))
                    print(r.content[0].text)
                except Exception as e:  # noqa: BLE001
                    print(f"REFUSED as {role}: {leaf(e)}"); return 1
    except BaseException as e:  # noqa: BLE001
        why = "HTTP 401 (token rejected)" if 401 in seen else str(leaf(e))
        print(f"REFUSED as {role}: {why}"); return 1
    return 0

sys.exit(asyncio.run(main()))
