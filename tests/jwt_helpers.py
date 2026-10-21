import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

TEAM = "rrtest.cloudflareaccess.com"
AUD = "aud-123"
EMAIL = "dh7777777@gmail.com"
KID = "k1"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Signing:
    def __init__(self, key):
        self.key = key


class FakeJwks:
    def __init__(self, fail=False):
        self.fail = fail

    def get_signing_key_from_jwt(self, token):
        if self.fail:
            raise jwt.PyJWKClientError("jwks unreachable")
        kid = jwt.get_unverified_header(token).get("kid")
        if kid != KID:
            raise jwt.PyJWKClientError("unknown kid")
        return _Signing(_KEY.public_key())


def make_token(key=None, kid=KID, alg="RS256", **claims):
    now = int(time.time())
    body = {"iss": f"https://{TEAM}", "aud": [AUD], "email": EMAIL, "sub": "u1",
            "iat": now, "nbf": now, "exp": now + 600}
    body.update(claims)
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, key or _KEY, algorithm=alg, headers={"kid": kid})


def other_key():
    return _OTHER
