from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

from app.domain.business_industries import (
    SCHEDULING_BUSINESS_TYPES,
    normalize_business_type,
)


PUBLIC_CHATBOT_CAPABILITIES: Final = frozenset({
    "answer_business_questions",
    "search_products_services",
    "recommend_products_services",
    "capture_lead",
    "lookup_available_appointments",
    "book_appointment",
    "lookup_order_status",
    "request_human_handoff",
})

SCHEDULING_CAPABILITIES: Final = frozenset({
    "lookup_available_appointments",
    "book_appointment",
})

PUBLIC_CAPABILITY_FEATURES: Final = MappingProxyType({
    "lookup_available_appointments": "scheduling",
    "book_appointment": "scheduling",
})

BUSINESS_FEATURE_POLICY: Final = MappingProxyType({
    "scheduling": SCHEDULING_BUSINESS_TYPES,
})

DEFAULT_PUBLIC_CAPABILITIES: Final = (
    "answer_business_questions",
    "search_products_services",
    "recommend_products_services",
    "capture_lead",
    "request_human_handoff",
)

CLINICAL_LANGUAGE: Final = re.compile(
    r"\b(?:diagnos(?:e|is)|prescri(?:be|ption)|dosage|dose|medication|"
    r"treatment plan|medical emergency|chest pain|bleeding heavily|"
    r"suicid(?:e|al)|is this cancer|what disease)\b",
    re.IGNORECASE,
)


def business_feature_enabled(business_type: str, feature: str) -> bool:
    return normalize_business_type(business_type) in BUSINESS_FEATURE_POLICY.get(
        feature, frozenset()
    )


def available_public_capabilities(business_type: str) -> tuple[str, ...]:
    return tuple(sorted(
        capability
        for capability in PUBLIC_CHATBOT_CAPABILITIES
        if (
            (feature := PUBLIC_CAPABILITY_FEATURES.get(capability)) is None
            or business_feature_enabled(business_type, feature)
        )
    ))


def resolve_public_capabilities(
    configured: list[str] | tuple[str, ...], business_type: str
) -> tuple[str, ...]:
    available = frozenset(available_public_capabilities(business_type))
    normalized = tuple(dict.fromkeys(configured))
    if any(value not in PUBLIC_CHATBOT_CAPABILITIES for value in normalized):
        raise ValueError("Unsupported public chatbot capability")
    if any(value not in available for value in normalized):
        raise ValueError("Capability is unavailable for this business")
    dependencies = {
        "recommend_products_services": "search_products_services",
        "book_appointment": "lookup_available_appointments",
    }
    if any(
        capability in normalized and dependency not in normalized
        for capability, dependency in dependencies.items()
    ):
        raise ValueError("Public chatbot capability dependency is missing")
    return tuple(sorted(normalized))


def normalize_allowed_hostname(value: str) -> str:
    raw = value.strip()
    if not raw or len(raw) > 253:
        raise ValueError("Domain must be between 1 and 253 characters")
    if any(character in raw for character in ("/", "\\", "?", "#", "@", "*")):
        raise ValueError("Use an exact hostname without paths or wildcards")
    if ":" in raw:
        # Colons imply a scheme, credentials, port, or IPv6 literal. Public
        # deployment domains are deliberately modeled as exact hostnames only.
        raise ValueError("Ports, schemes, and address literals are not allowed")
    try:
        hostname = raw.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError:
        raise ValueError("Domain is invalid") from None
    if hostname == "localhost":
        return hostname
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP address literals are not allowed")
    labels = hostname.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("Domain is invalid")
    return hostname


def normalize_allowed_hostnames(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        hostname = normalize_allowed_hostname(value)
        if hostname not in seen:
            seen.add(hostname)
            normalized.append(hostname)
    if len(normalized) > 50:
        raise ValueError("At most 50 allowed domains may be configured")
    return normalized


def request_origin(value: str | None, referer: str | None = None) -> tuple[str, str]:
    candidate = value or referer
    if candidate is None:
        raise ValueError("A browser origin is required")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Browser origin is invalid")
    if parsed.username or parsed.password:
        raise ValueError("Browser origin is invalid")
    if value is not None and (
        parsed.path not in {"", "/"} or parsed.query or parsed.fragment
    ):
        raise ValueError("Browser origin is invalid")
    hostname = normalize_allowed_hostname(parsed.hostname)
    default_port = 80 if parsed.scheme == "http" else 443
    port = parsed.port
    serialized = f"{parsed.scheme}://{hostname}"
    if port is not None and port != default_port:
        serialized += f":{port}"
    return hostname, serialized


def create_widget_public_id() -> str:
    return secrets.token_urlsafe(32)


def create_public_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_public_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def public_reference(secret: bytes, namespace: str, value: UUID | str) -> str:
    digest = hmac.new(
        secret, f"{namespace}:{value}".encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest[:18]).decode("ascii").rstrip("=")


def looks_clinical(message: str) -> bool:
    return bool(CLINICAL_LANGUAGE.search(message))
