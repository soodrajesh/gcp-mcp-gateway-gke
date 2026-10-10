"""The server's trust boundary: only the gateway's identity, with this server's audience, gets in."""
import asyncio, sys, time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from mcpkit.asgi import RequireGatewayIdentity  # noqa: E402
from mcpkit.audit import caller_var  # noqa: E402
from mcpkit.auth import AuthError, GatewayIdentityVerifier, GoogleCerts  # noqa: E402

GW = "mcp-gateway@proj.iam.gserviceaccount.com"


def keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k, k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


KEY, PUB = keypair()


def token(key=KEY, kid="k1", **over):
    c = {"iss": "https://accounts.google.com", "aud": "mcp-tickets", "email": GW, "email_verified": True,
         "iat": int(time.time()), "exp": int(time.time()) + 600, **over}
    return "Bearer " + jwt.encode(c, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def v():
    return GatewayIdentityVerifier("mcp-tickets", {GW}, GoogleCerts(lambda: {"k1": PUB}))


def reject(v, header, status):
    with pytest.raises(AuthError) as e:
        v.verify(header)
    assert e.value.status == status


def test_valid_gateway_token_is_accepted(v):
    assert v.verify(token()) == GW


def test_missing_and_garbage_are_401(v):
    reject(v, None, 401); reject(v, "Bearer abc", 401); reject(v, "Basic xyz", 401)


def test_token_for_another_server_is_refused(v):
    reject(v, token(aud="mcp-finops"), 401)          # genuine, correctly signed, but not for this server


def test_expired_wrong_issuer_unverified_email(v):
    reject(v, token(exp=int(time.time()) - 3600), 401)
    reject(v, token(iss="https://evil.example"), 401)
    reject(v, token(email_verified=False), 401)


def test_signed_by_an_untrusted_key_is_401(v):
    other, _ = keypair()
    reject(v, token(key=other), 401)


def test_valid_google_token_from_a_different_identity_is_403(v):
    reject(v, token(email="someone-else@proj.iam.gserviceaccount.com"), 403)


def test_rotated_signing_key_is_picked_up_once():
    new_key, new_pub = keypair()
    state = {"n": 0}

    def fetch():
        state["n"] += 1
        return {"k1": PUB} if state["n"] == 1 else {"k1": PUB, "k2": new_pub}

    v = GatewayIdentityVerifier("mcp-tickets", {GW}, GoogleCerts(fetch))
    assert v.verify(token()) == GW                   # populates the cache with k1 only
    assert v.verify(token(key=new_key, kid="k2")) == GW
    assert state["n"] == 2                           # exactly one refetch


def run(app, path, auth=None):
    sent = []

    async def send(m): sent.append(m)
    async def receive(): return {"type": "http.request", "body": b""}
    scope = {"type": "http", "path": path, "headers": [(b"authorization", auth.encode())] if auth else []}
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"]


def test_middleware_gates_everything_but_healthz(v):
    seen = []

    async def inner(scope, receive, send):
        seen.append(caller_var.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = RequireGatewayIdentity(inner, v, "tickets")
    assert run(app, "/healthz") == 200 and seen == []
    assert run(app, "/mcp") == 401 and seen == []
    assert run(app, "/mcp", token(aud="mcp-k8s")) == 401 and seen == []
    assert run(app, "/mcp", token(email="x@y.iam.gserviceaccount.com")) == 403 and seen == []
    assert run(app, "/mcp", token()) == 200 and seen == [GW]       # positive control: the gateway gets through
