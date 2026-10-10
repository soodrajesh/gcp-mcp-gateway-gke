"""Service-to-service authentication for an MCP server that sits behind the gateway.

The gateway authenticates to each server as ITSELF with a Google-signed ID token (keyless, Workload Identity) whose
audience is that one server. The server therefore checks, on every request:
  * the signature, against Google's published keys (cached; refetched once if the key id is unknown),
  * the issuer is Google, the token is not expired,
  * the AUDIENCE is this server (a token minted for another server is refused),
  * the caller is the gateway's service account, and its email is verified.
Anything else is refused before a tool runs. The end user's identity and role are the gateway's concern; the
server only trusts "this request came from the gateway"."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping

import requests
from google.auth import exceptions as ga_exceptions
from google.auth import jwt as ga_jwt

CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
log = logging.getLogger("mcpkit.auth")


class AuthError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


class GoogleCerts:
    """Google's signing certs with a TTL cache. Fetching on every request would add a network call to each tool call."""

    def __init__(self, fetch: Callable[[], Mapping[str, str]] | None = None, ttl: float = 3600.0):
        self._fetch = fetch or (lambda: requests.get(CERTS_URL, timeout=5).json())
        self._ttl, self._certs, self._at = ttl, {}, 0.0

    def get(self, force: bool = False) -> Mapping[str, str]:
        if force or not self._certs or time.monotonic() - self._at > self._ttl:
            self._certs, self._at = dict(self._fetch()), time.monotonic()
        return self._certs


class GatewayIdentityVerifier:
    def __init__(self, audience: str, allowed_emails: set[str], certs: GoogleCerts | None = None):
        self.audience, self.allowed = audience, {e.lower() for e in allowed_emails}
        self.certs = certs or GoogleCerts()

    def verify(self, authorization: str | None) -> str:
        """Return the caller's email, or raise AuthError (401 = no/invalid credential, 403 = valid but not allowed)."""
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthError(401, "missing_bearer_token")
        token = authorization[7:].strip()
        try:
            try:
                claims = ga_jwt.decode(token, certs=self.certs.get(), audience=self.audience, clock_skew_in_seconds=10)
            except ValueError as first:
                if "Certificate for key id" not in str(first):
                    raise
                claims = ga_jwt.decode(token, certs=self.certs.get(force=True), audience=self.audience, clock_skew_in_seconds=10)
        except (ValueError, ga_exceptions.GoogleAuthError, requests.RequestException, json.JSONDecodeError) as e:
            log.warning("token rejected: %s", type(e).__name__ + ": " + str(e)[:160])
            raise AuthError(401, "invalid_token") from e
        if claims.get("iss") not in ISSUERS:
            raise AuthError(401, "wrong_issuer")
        email = str(claims.get("email", "")).lower()
        if not email or claims.get("email_verified") is not True:
            raise AuthError(401, "token_has_no_verified_email")
        if email not in self.allowed:
            log.warning("caller not allowed: %s", email)
            raise AuthError(403, "caller_not_allowed")
        return email
