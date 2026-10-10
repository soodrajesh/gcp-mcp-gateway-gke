"""Shared helpers: fetch tokens from Keycloak, open an MCP session through the gateway, capture HTTP status/headers."""
import base64, json, os, urllib.parse, urllib.request
from pathlib import Path

from mcp.client.client import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

ROOT = Path(__file__).resolve().parent.parent
GATEWAY = os.environ.get("GATEWAY", "http://localhost:18080")
KEYCLOAK = os.environ.get("KEYCLOAK", "http://localhost:18081")
REALM = os.environ.get("REALM", "mcp")
MCP_URL = f"{GATEWAY}/mcp"
TOKEN_URL = f"{KEYCLOAK}/realms/{REALM}/protocol/openid-connect/token"


def secret(client: str) -> str:
    return (ROOT / ".secrets" / client).read_text().strip()


def get_token(client: str, client_secret: str | None = None):
    """OAuth2 client-credentials grant against Keycloak. Returns (http_status, json_body)."""
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client,
                                   "client_secret": client_secret or secret(client)}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def claims(token: str) -> dict:
    p = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def http_get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {}


def leaf(e: BaseException) -> BaseException:
    return leaf(e.exceptions[0]) if hasattr(e, "exceptions") else e


async def session(token: str | None, fn):
    """Run fn(client) with a bearer token. Returns (result_or_exception, [(status, www-authenticate), ...])."""
    seen: list[tuple[int, str | None]] = []

    async def hook(resp):
        seen.append((resp.status_code, resp.headers.get("www-authenticate")))

    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"} if token else {})
    http.event_hooks["response"].append(hook)
    try:
        async with http:
            async with Client(streamable_http_client(MCP_URL, http_client=http)) as c:
                return await fn(c), seen
    except BaseException as e:  # noqa: BLE001 - callers assert on the status, whatever the SDK raises
        return e, seen


async def list_tools(c):
    return sorted(t.name for t in (await c.list_tools()).tools)


class ToolFailed(Exception):
    """The server ran the tool and reported an error (bad argument, namespace not allowed, ...)."""


def payload(r):
    """A tool result as plain Python: the structured value if there is one, else the text items joined."""
    sc = getattr(r, "structured_content", None)
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    text = "\n".join(c.text for c in r.content)
    try:
        return json.loads(text)      # a tool that returns a dict arrives as one JSON text item
    except ValueError:
        return text


async def call(c, name, **args):
    """The tool's result, or the exception it raised (a refusal is a normal outcome to assert on)."""
    try:
        r = await c.call_tool(name, args)
    except Exception as e:  # noqa: BLE001
        return e
    if r.is_error:
        return ToolFailed(" ".join(getattr(c_, "text", "") for c_ in r.content))
    return payload(r)
