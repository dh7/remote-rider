import time

import pytest

from gateway_auth import AccessVerifier, AuthError
from tests.jwt_helpers import AUD, EMAIL, TEAM, FakeJwks, make_token, other_key


def v(jwks=None):
    return AccessVerifier(TEAM, AUD, EMAIL, jwk_client=jwks or FakeJwks())


def test_valid_token():
    assert v().verify(make_token())["email"] == EMAIL


@pytest.mark.parametrize("token", [
    None, "", "garbage",
    make_token(exp=int(time.time()) - 120),
    make_token(nbf=int(time.time()) + 600),
    make_token(aud=["other"]),
    make_token(iss="https://evil.cloudflareaccess.com"),
    make_token(email="someone@else.com"),
    make_token(email=None),
    make_token(exp=None),
    make_token(key=other_key()),
    make_token(kid="unknown"),
    make_token(key="a-shared-secret-of-at-least-32-bytes!!", alg="HS256"),
], ids=["none", "empty", "garbage", "expired", "not-yet", "aud", "iss", "email", "no-email",
        "no-exp", "wrong-key", "unknown-kid", "hs256"])
def test_rejected(token):
    with pytest.raises(AuthError):
        v().verify(token)


def test_email_case_insensitive():
    assert v().verify(make_token(email=EMAIL.upper()))


def test_jwks_failure_denies():
    with pytest.raises(AuthError):
        v(FakeJwks(fail=True)).verify(make_token())


@pytest.mark.parametrize("team", ["evil.com", "", "x.cloudflareaccess.com.evil.com"])
def test_bad_team_domain_rejected(team):
    with pytest.raises(ValueError):
        AccessVerifier(team, AUD, EMAIL, jwk_client=FakeJwks())
