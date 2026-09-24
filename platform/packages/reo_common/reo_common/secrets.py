"""Secrets vault abstraction (TR-AUTH-01 / FR-CON-002: secrets are written to
a vault and never returned in cleartext once stored; application code only
ever holds an opaque reference).

Two implementations share one interface so a tenant can be moved from local
Fernet-encrypted storage to AWS Secrets Manager/KMS without changing any
calling code (Connector Studio, Credential Broker).
"""

from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod

from cryptography.fernet import Fernet

from .config import get_settings

settings = get_settings()


class SecretsProvider(ABC):
    @abstractmethod
    def store(self, plaintext: dict) -> str:
        """Store a secret payload, return an opaque encrypted blob/reference."""

    @abstractmethod
    def reveal_for_use(self, reference: str) -> dict:
        """Decrypt for *server-side* use only (e.g. making an outbound call).
        Never expose the return value of this over an API response."""

    def masked_summary(self, reference: str) -> dict:
        """Safe-to-display summary — never includes secret material."""
        try:
            data = self.reveal_for_use(reference)
        except Exception:
            return {"status": "unreadable"}
        summary = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > 4:
                summary[k] = f"****{v[-4:]}"
            else:
                summary[k] = "****"
        return summary


class LocalFernetSecretsProvider(SecretsProvider):
    """Local dev/demo implementation: Fernet symmetric encryption with a
    master key from config. The encrypted blob is what gets stored in
    `CredentialRef.encrypted_payload` — the plaintext never touches the DB."""

    def __init__(self, master_key: str | None = None):
        key = master_key or settings.vault_master_key
        # Fernet requires a 32-byte urlsafe-base64 key; derive deterministically if a raw string was given.
        try:
            Fernet(key.encode())
            self._fernet = Fernet(key.encode())
        except Exception:
            derived = base64.urlsafe_b64encode(key.encode("utf-8").ljust(32, b"0")[:32])
            self._fernet = Fernet(derived)

    def store(self, plaintext: dict) -> str:
        raw = json.dumps(plaintext).encode("utf-8")
        return self._fernet.encrypt(raw).decode("utf-8")

    def reveal_for_use(self, reference: str) -> dict:
        raw = self._fernet.decrypt(reference.encode("utf-8"))
        return json.loads(raw)


class AwsSecretsManagerProvider(SecretsProvider):
    """AWS deployment implementation (Terraform provisions the Secrets Manager
    resources; this class is exercised only when `secrets_provider=aws`).
    Requires `boto3`, which is a runtime-only dependency for AWS deployments.
    """

    def __init__(self, region: str | None = None):
        import boto3  # local import: not required for the local/dev path

        self._client = boto3.client("secretsmanager", region_name=region or settings.s3_region)

    def store(self, plaintext: dict) -> str:
        resp = self._client.create_secret(Name=f"reo/connector/{id(plaintext)}", SecretString=json.dumps(plaintext))
        return resp["ARN"]

    def reveal_for_use(self, reference: str) -> dict:
        resp = self._client.get_secret_value(SecretId=reference)
        return json.loads(resp["SecretString"])


def get_secrets_provider() -> SecretsProvider:
    if settings.secrets_provider == "aws":
        return AwsSecretsManagerProvider()
    return LocalFernetSecretsProvider()
