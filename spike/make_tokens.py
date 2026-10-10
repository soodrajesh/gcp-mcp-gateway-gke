"""Generate a throwaway signing key, its JWKS, and the test tokens. Output dir is gitignored."""
import base64, json, sys, time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

out = Path(sys.argv[1]); out.mkdir(exist_ok=True)
b64 = lambda n: base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()
key = lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)

good, other = key(), key()
pub = good.public_key().public_numbers()
(out / "jwks.json").write_text(json.dumps({"keys": [
    {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "spike-1", "n": b64(pub.n), "e": b64(pub.e)}]}))

now = int(time.time())
def token(name, signer=good, **over):
    claims = {"iss": "spike.local", "aud": "mcp-gateway", "sub": name, "role": name,
              "iat": now, "nbf": now - 5, "exp": now + 3600, **over}
    (out / f"{name}.jwt").write_text(jwt.encode(claims, signer, algorithm="RS256", headers={"kid": "spike-1"}))

token("viewer"); token("operator")
token("expired", role="operator", exp=now - 3600)
token("wrong_audience", role="operator", aud="some-other-service")
token("wrong_signer", signer=other, role="operator")  # right claims, key the gateway does not trust
