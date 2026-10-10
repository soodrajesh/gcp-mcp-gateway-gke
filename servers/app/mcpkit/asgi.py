"""ASGI middleware: refuse every request that does not carry the gateway's identity, except the health probe."""
from __future__ import annotations

import json

from .audit import caller_var, emit
from .auth import AuthError, GatewayIdentityVerifier


class RequireGatewayIdentity:
    def __init__(self, app, verifier: GatewayIdentityVerifier, server: str):
        self.app, self.verifier, self.server = app, verifier, server

    async def _reply(self, send, status: int, body: dict):
        data = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
        await send({"type": "http.response.body", "body": data})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/healthz":
            return await self._reply(send, 200, {"status": "ok"})
        auth = dict(scope["headers"]).get(b"authorization", b"").decode() or None
        try:
            email = self.verifier.verify(auth)
        except AuthError as e:
            emit(event="request_rejected", server=self.server, status=e.status, reason=e.reason, path=scope["path"])
            # The reason is deliberately generic to the caller; the detail is in our log only.
            return await self._reply(send, e.status, {"error": "unauthorized" if e.status == 401 else "forbidden"})
        token = caller_var.set(email)
        try:
            await self.app(scope, receive, send)
        finally:
            caller_var.reset(token)
