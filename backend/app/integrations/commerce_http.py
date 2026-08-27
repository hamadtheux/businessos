from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import json
from ipaddress import ip_address
import socket
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from app.exceptions.commerce import CommerceProviderError, CommerceValidationError


MAX_PROVIDER_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 250_000
_BLOCKED_HOSTS = frozenset({"localhost", "localhost.localdomain", "metadata.google.internal"})


async def resolve_public_host(host: str) -> tuple[str, ...]:
    """Resolve every address and fail closed if any target is non-public."""
    normalized = host.rstrip(".").casefold()
    if normalized in _BLOCKED_HOSTS or normalized.endswith((".localhost", ".local")):
        raise CommerceValidationError("unsafe_provider_url")
    try:
        literal = ip_address(normalized.strip("[]"))
    except ValueError:
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                normalized, 443, type=socket.SOCK_STREAM,
            )
        except OSError:
            raise CommerceProviderError("provider_unavailable", retryable=True) from None
        addresses = tuple(sorted({record[4][0] for record in records}))
        if not addresses or any(not ip_address(value).is_global for value in addresses):
            raise CommerceValidationError("unsafe_provider_url")
        return addresses
    if not literal.is_global:
        raise CommerceValidationError("unsafe_provider_url")
    return (str(literal),)


def validate_public_https_url(url: str, *, allowed_host: str | None = None) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise CommerceValidationError("unsafe_provider_url")
    host = parsed.hostname.rstrip(".").casefold()
    if allowed_host and host != allowed_host.rstrip(".").casefold():
        raise CommerceValidationError("unsafe_provider_url")
    if host in _BLOCKED_HOSTS or host.endswith((".localhost", ".local")):
        raise CommerceValidationError("unsafe_provider_url")
    try:
        address = ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise CommerceValidationError("unsafe_provider_url")
    return url


class SafeCommerceHttpClient:
    """Bounded JSON transport with SSRF and redirect protections."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        resolver: Callable[[str], Awaitable[tuple[str, ...]]] = resolve_public_host,
        validate_dns: bool = True,
    ) -> None:
        self._client = client
        self._resolver = resolver
        self._validate_dns = validate_dns

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        json_body: Mapping[str, object] | None = None,
        allowed_host: str | None = None,
    ) -> tuple[Any, httpx.Headers]:
        validate_public_https_url(url, allowed_host=allowed_host)
        host = urlsplit(url).hostname or ""
        if self._validate_dns:
            await self._resolver(host)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(DEFAULT_TIMEOUT_SECONDS),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        try:
            async with client.stream(
                method,
                url,
                headers={**(headers or {}), "Accept": "application/json"},
                params=params,
                json=json_body,
                timeout=httpx.Timeout(DEFAULT_TIMEOUT_SECONDS),
            ) as response:
                if 300 <= response.status_code < 400:
                    raise CommerceProviderError("unsafe_redirect", retryable=False)
                if response.status_code == 401:
                    raise CommerceProviderError("authentication_failed", retryable=False)
                if response.status_code == 403:
                    raise CommerceProviderError("authorization_required", retryable=False)
                if response.status_code == 429:
                    raise CommerceProviderError(
                        "rate_limited", retryable=True,
                        retry_after_seconds=_retry_after_seconds(response.headers.get("retry-after")),
                    )
                if response.status_code in {408, 425}:
                    raise CommerceProviderError("temporary_provider_failure", retryable=True)
                if response.status_code >= 500:
                    raise CommerceProviderError(
                        "temporary_provider_failure", retryable=True,
                        retry_after_seconds=_retry_after_seconds(response.headers.get("retry-after")),
                    )
                if response.status_code == 404:
                    raise CommerceProviderError("provider_not_found", retryable=False)
                if response.status_code in {400, 409, 422}:
                    raise CommerceProviderError("provider_validation_error", retryable=False)
                if response.status_code >= 400:
                    raise CommerceProviderError("request_failed", retryable=False)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
                if content_type not in {"application/json", "application/graphql-response+json"}:
                    raise CommerceProviderError("invalid_content_type", retryable=False)
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise CommerceProviderError("response_too_large", retryable=False)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_PROVIDER_RESPONSE_BYTES:
                        raise CommerceProviderError("response_too_large", retryable=False)
                response_headers = httpx.Headers(response.headers)
        except CommerceProviderError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.StreamError):
            raise CommerceProviderError("provider_unavailable", retryable=True) from None
        finally:
            if owns_client:
                await client.aclose()
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise CommerceProviderError("invalid_response", retryable=False) from None
        _validate_json_shape(payload)
        return payload, response_headers


def parse_bounded_json_bytes(body: bytes, *, max_bytes: int) -> Any:
    """Parse an already bounded webhook body with structural limits."""
    if not body or len(body) > max_bytes:
        raise CommerceProviderError("invalid_response", retryable=False)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise CommerceProviderError("invalid_response", retryable=False) from None
    _validate_json_shape(payload)
    return payload


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.isdigit():
        return max(1, min(int(normalized), 86_400))
    try:
        parsed = parsedate_to_datetime(normalized)
    except (TypeError, ValueError, OverflowError):
        return None
    parsed = parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    return max(1, min(int((parsed - datetime.now(UTC)).total_seconds()), 86_400))


def _validate_json_shape(payload: Any) -> None:
    nodes = 0
    pending: list[tuple[Any, int]] = [(payload, 1)]
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise CommerceProviderError("invalid_response", retryable=False)
        if isinstance(value, Mapping):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)


def provider_url(base_url: str, path: str) -> str:
    base = validate_public_https_url(base_url).rstrip("/") + "/"
    value = urljoin(base, path.lstrip("/"))
    validate_public_https_url(value, allowed_host=urlsplit(base).hostname)
    return value
