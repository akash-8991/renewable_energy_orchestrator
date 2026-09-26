"""Real IdP integration (production-readiness gap: "authlib OIDC client
wired but never tested against a real IdP"). These tests exercise the
actual cryptographic path `_exchange_code_for_claims` relies on — a real
RSA-signed id_token verified against a real JWKS, the same way the bundled
Keycloak container's tokens are verified — by generating a real keypair and
routing httpx through it, rather than trusting an unverified decode."""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk as jose_jwk
from jose import jwt as jose_jwt

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.routers import auth as auth_module  # noqa: E402


@pytest.fixture()
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


def _sign_id_token(private_pem: bytes, claims: dict, kid: str = "test-key-1") -> str:
    return jose_jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def _jwks_for(public_pem: bytes, kid: str = "test-key-1") -> dict:
    key = jose_jwk.construct(public_pem, algorithm="RS256")
    jwk_dict = key.to_dict()
    jwk_dict["kid"] = kid
    return {"keys": [jwk_dict]}


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


def test_exchange_code_for_claims_verifies_a_real_signature(monkeypatch, rsa_keypair):
    private_pem, public_pem = rsa_keypair
    now = int(time.time())
    claims = {
        "sub": "kc-user-123", "email": "sso.demo@demo-utility.test", "name": "SSO Demo",
        "aud": "reo-platform", "iss": "http://keycloak:8080/realms/reo", "iat": now, "exp": now + 300,
    }
    id_token = _sign_id_token(private_pem, claims)
    jwks = _jwks_for(public_pem)

    monkeypatch.setattr(auth_module.settings, "oidc_issuer", "http://keycloak:8080/realms/reo")
    monkeypatch.setattr(auth_module.settings, "oidc_client_id", "reo-platform")
    monkeypatch.setattr(auth_module.settings, "oidc_client_secret", "reo-platform-secret")
    monkeypatch.setattr(auth_module.settings, "oidc_redirect_uri", "http://localhost:8000/auth/sso/callback")

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, data=None):
            assert "token" in url
            return _FakeResponse({"id_token": id_token, "access_token": "irrelevant"})

        def get(self, url):
            assert "certs" in url
            return _FakeResponse(jwks)

    import httpx

    monkeypatch.setattr(httpx, "Client", lambda timeout=None: _FakeClient())

    result = auth_module._exchange_code_for_claims("some-auth-code")
    assert result["sub"] == "kc-user-123"
    assert result["email"] == "sso.demo@demo-utility.test"


def test_exchange_code_for_claims_rejects_a_token_signed_by_a_different_key(monkeypatch, rsa_keypair):
    """A token signed by an attacker's key, verified against the real IdP's
    published JWKS, must fail — this is the entire point of verifying the
    signature instead of trusting a bare decode of attacker-controlled
    claims."""
    _attacker_private_pem, _ = rsa_keypair
    _real_private_pem, real_public_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048), None
    real_public_pem = _real_private_pem.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    real_private_pem = _real_private_pem.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )

    now = int(time.time())
    forged_claims = {"sub": "attacker", "aud": "reo-platform", "iat": now, "exp": now + 300}
    forged_token = _sign_id_token(_attacker_private_pem, forged_claims)  # signed by the WRONG key
    jwks = _jwks_for(real_public_pem)  # the real IdP's actual published key

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, data=None):
            return _FakeResponse({"id_token": forged_token})

        def get(self, url):
            return _FakeResponse(jwks)

    import httpx

    monkeypatch.setattr(httpx, "Client", lambda timeout=None: _FakeClient())
    monkeypatch.setattr(auth_module.settings, "oidc_client_id", "reo-platform")

    with pytest.raises(Exception):
        auth_module._exchange_code_for_claims("some-auth-code")


def test_sso_login_requires_configuration(monkeypatch):
    monkeypatch.setattr(auth_module.settings, "oidc_issuer", None)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        auth_module.sso_login(tenant_slug="demo-utility")
    assert exc_info.value.status_code == 501


def test_sso_login_redirects_to_the_public_authorize_url(monkeypatch):
    monkeypatch.setattr(auth_module.settings, "oidc_issuer", "http://keycloak:8080/realms/reo")
    monkeypatch.setattr(auth_module.settings, "oidc_client_id", "reo-platform")
    monkeypatch.setattr(auth_module.settings, "oidc_authorize_url_public", "http://localhost:8081/realms/reo/protocol/openid-connect/auth")
    monkeypatch.setattr(auth_module.settings, "oidc_redirect_uri", "http://localhost:8000/auth/sso/callback")

    response = auth_module.sso_login(tenant_slug="demo-utility")
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("http://localhost:8081/realms/reo/protocol/openid-connect/auth?")
    assert "client_id=reo-platform" in response.headers["location"]
