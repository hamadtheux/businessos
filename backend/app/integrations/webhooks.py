from __future__ import annotations

import hashlib
import hmac
from typing import Mapping, Protocol


class WebhookSignatureVerifier(Protocol):
    def verify(self, *, body: bytes, headers: Mapping[str, str]) -> bool: ...


class DisabledWebhookSignatureVerifier:
    def verify(self, *, body: bytes, headers: Mapping[str, str]) -> bool:
        return False


class MetaWebhookSignatureVerifier:
    """Meta X-Hub-Signature-256 verification using a server-only app secret."""

    def __init__(self, signing_secret: str) -> None:
        if not signing_secret:
            raise ValueError("A signing secret is required")
        self._secret = signing_secret.encode("utf-8")

    def verify(self, *, body: bytes, headers: Mapping[str, str]) -> bool:
        supplied = headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, expected)
