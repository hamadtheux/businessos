from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Mapping, Protocol, runtime_checkable
from uuid import UUID

from app.exceptions.integration import IntegrationCredentialUnavailableError

if TYPE_CHECKING:
    from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class CredentialMaterial:
    values: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.values or len(self.values) > 12:
            raise ValueError("Credential material is invalid")
        if any(not key or len(key) > 64 or not value or len(value) > 16_384 for key, value in self.values.items()):
            raise ValueError("Credential material is invalid")


@runtime_checkable
class IntegrationCredentialStore(Protocol):
    async def store(self, *, business_id: UUID, connector_type: str, purpose: str, material: CredentialMaterial) -> str: ...
    async def retrieve(self, reference: str, *, business_id: UUID, connector_type: str, purpose: str) -> CredentialMaterial: ...
    async def rotate(self, reference: str, *, business_id: UUID, connector_type: str, purpose: str, material: CredentialMaterial) -> None: ...
    async def revoke(self, reference: str, *, business_id: UUID, connector_type: str, purpose: str) -> None: ...


class DisabledIntegrationCredentialStore:
    """Production-safe default when no real Vault/KMS backend exists."""

    async def store(self, **_: object) -> str:
        raise IntegrationCredentialUnavailableError("credential_unavailable")

    async def retrieve(self, *_: object, **__: object) -> CredentialMaterial:
        raise IntegrationCredentialUnavailableError("credential_unavailable")

    async def rotate(self, *_: object, **__: object) -> None:
        raise IntegrationCredentialUnavailableError("credential_unavailable")

    async def revoke(self, *_: object, **__: object) -> None:
        raise IntegrationCredentialUnavailableError("credential_unavailable")


class InMemoryIntegrationCredentialStore:
    """Explicitly test-only; never selected from production configuration."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[UUID, str, str, CredentialMaterial]] = {}

    async def store(self, *, business_id: UUID, connector_type: str, purpose: str, material: CredentialMaterial) -> str:
        reference = f"test-credential:{token_urlsafe(24)}"
        self._values[reference] = (business_id, connector_type, purpose, material)
        return reference

    async def retrieve(self, reference: str, *, business_id: UUID, connector_type: str, purpose: str) -> CredentialMaterial:
        value = self._values.get(reference)
        if value is None or value[:3] != (business_id, connector_type, purpose):
            raise IntegrationCredentialUnavailableError("credential_unavailable")
        return CredentialMaterial(values=dict(value[3].values))

    async def rotate(self, reference: str, *, business_id: UUID, connector_type: str, purpose: str, material: CredentialMaterial) -> None:
        await self.retrieve(reference, business_id=business_id, connector_type=connector_type, purpose=purpose)
        self._values[reference] = (business_id, connector_type, purpose, material)

    async def revoke(self, reference: str, *, business_id: UUID, connector_type: str, purpose: str) -> None:
        await self.retrieve(reference, business_id=business_id, connector_type=connector_type, purpose=purpose)
        del self._values[reference]


class AwsSecretsManagerIntegrationCredentialStore:
    """Tenant-bound encrypted credential storage using AWS Secrets Manager.

    AWS credentials are resolved by the SDK's workload-identity chain. They
    are never accepted through tenant APIs or stored in this object.
    """

    def __init__(
        self,
        *,
        region_name: str,
        prefix: str,
        kms_key_id: str | None = None,
        client: object | None = None,
    ) -> None:
        self._region_name = region_name
        self._prefix = prefix.strip("/")
        self._kms_key_id = kms_key_id
        self._client = client

    def _secrets_client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "secretsmanager", region_name=self._region_name
            )
        return self._client

    async def store(
        self,
        *,
        business_id: UUID,
        connector_type: str,
        purpose: str,
        material: CredentialMaterial,
    ) -> str:
        _validate_binding(connector_type, purpose)
        reference = (
            f"{self._prefix}/{business_id}/{connector_type}/{purpose}/"
            f"{token_urlsafe(18)}"
        )
        payload = _credential_payload(
            business_id, connector_type, purpose, material
        )
        arguments: dict[str, object] = {
            "Name": reference,
            "SecretString": payload,
            "Tags": [
                {"Key": "aibos:business", "Value": str(business_id)},
                {"Key": "aibos:connector", "Value": connector_type},
                {"Key": "aibos:purpose", "Value": purpose},
            ],
        }
        if self._kms_key_id:
            arguments["KmsKeyId"] = self._kms_key_id
        try:
            await asyncio.to_thread(
                self._secrets_client().create_secret, **arguments
            )
        except Exception:
            raise IntegrationCredentialUnavailableError(
                "credential_unavailable"
            ) from None
        return reference

    async def retrieve(
        self,
        reference: str,
        *,
        business_id: UUID,
        connector_type: str,
        purpose: str,
    ) -> CredentialMaterial:
        _validate_reference(reference, self._prefix)
        try:
            response = await asyncio.to_thread(
                self._secrets_client().get_secret_value, SecretId=reference
            )
            payload = response.get("SecretString")
            if not isinstance(payload, str):
                raise ValueError
            decoded = json.loads(payload)
            if (
                decoded.get("version") != 1
                or decoded.get("business_id") != str(business_id)
                or decoded.get("connector_type") != connector_type
                or decoded.get("purpose") != purpose
                or not isinstance(decoded.get("values"), dict)
            ):
                raise ValueError
            values = decoded["values"]
            if any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
                raise ValueError
            return CredentialMaterial(values=dict(values))
        except Exception:
            raise IntegrationCredentialUnavailableError(
                "credential_unavailable"
            ) from None

    async def rotate(
        self,
        reference: str,
        *,
        business_id: UUID,
        connector_type: str,
        purpose: str,
        material: CredentialMaterial,
    ) -> None:
        await self.retrieve(
            reference,
            business_id=business_id,
            connector_type=connector_type,
            purpose=purpose,
        )
        try:
            await asyncio.to_thread(
                self._secrets_client().put_secret_value,
                SecretId=reference,
                SecretString=_credential_payload(
                    business_id, connector_type, purpose, material
                ),
            )
        except Exception:
            raise IntegrationCredentialUnavailableError(
                "credential_unavailable"
            ) from None

    async def revoke(
        self,
        reference: str,
        *,
        business_id: UUID,
        connector_type: str,
        purpose: str,
    ) -> None:
        await self.retrieve(
            reference,
            business_id=business_id,
            connector_type=connector_type,
            purpose=purpose,
        )
        try:
            await asyncio.to_thread(
                self._secrets_client().delete_secret,
                SecretId=reference,
                RecoveryWindowInDays=7,
            )
        except Exception:
            raise IntegrationCredentialUnavailableError(
                "credential_unavailable"
            ) from None


def build_credential_store(configuration: "Settings") -> IntegrationCredentialStore:
    if configuration.integration_credential_backend == "aws_secrets_manager":
        return AwsSecretsManagerIntegrationCredentialStore(
            region_name=configuration.integration_secret_region or "",
            prefix=configuration.integration_secret_prefix,
            kms_key_id=configuration.integration_secret_kms_key_id,
        )
    return DisabledIntegrationCredentialStore()


def _credential_payload(
    business_id: UUID,
    connector_type: str,
    purpose: str,
    material: CredentialMaterial,
) -> str:
    return json.dumps(
        {
            "version": 1,
            "business_id": str(business_id),
            "connector_type": connector_type,
            "purpose": purpose,
            "values": dict(material.values),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_binding(connector_type: str, purpose: str) -> None:
    for value in (connector_type, purpose):
        if (
            not value
            or len(value) > 64
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in value)
        ):
            raise IntegrationCredentialUnavailableError("credential_unavailable")


def _validate_reference(reference: str, prefix: str) -> None:
    if not reference.startswith(f"{prefix}/") or len(reference) > 255:
        raise IntegrationCredentialUnavailableError("credential_unavailable")


from app.core.config import settings

credential_store: IntegrationCredentialStore = build_credential_store(settings)
