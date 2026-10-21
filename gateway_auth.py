"""Cloudflare Access JWT verification for gateway.py. Every failure denies."""
from __future__ import annotations

import re
from typing import Any

import jwt

_TEAM_RE = re.compile(r"^[a-z0-9-]+\.cloudflareaccess\.com$")


class AuthError(Exception):
    pass


class AccessVerifier:
    def __init__(self, team_domain: str, audience: str, allowed_email: str, jwk_client=None, leeway: int = 30):
        if not _TEAM_RE.match(team_domain or ""):
            raise ValueError("CF_TEAM_DOMAIN must look like <team>.cloudflareaccess.com")
        if not audience or not allowed_email:
            raise ValueError("audience and allowed_email are required")
        self.issuer = f"https://{team_domain}"
        self.audience = audience
        self.allowed_email = allowed_email.strip().lower()
        self.leeway = leeway
        self._jwk = jwk_client or jwt.PyJWKClient(
            f"{self.issuer}/cdn-cgi/access/certs", cache_jwk_set=True, lifespan=300, timeout=5
        )

    def verify(self, token: str | None) -> dict[str, Any]:
        if not token:
            raise AuthError("missing token")
        try:
            key = self._jwk.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except Exception as exc:  # includes JWKS fetch failures: never allow on error
            raise AuthError("invalid token") from exc
        if str(claims.get("email", "")).strip().lower() != self.allowed_email:
            raise AuthError("email not allowed")
        return claims
