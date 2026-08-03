"""Network boundary validation and bounded response readers."""

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Iterable, Mapping
from urllib.parse import urlsplit


class UnsafeNetworkTargetError(ValueError):
    """Raised when a URL could access a non-public or unexpected target."""


def validate_public_https_url(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    """Validate an HTTPS URL before it reaches the HTTP adapter."""
    normalized = url.strip()
    parsed = urlsplit(normalized)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise UnsafeNetworkTargetError("URL must be public HTTPS without credentials")
    if parsed.port not in {None, 443}:
        raise UnsafeNetworkTargetError("URL must use the default HTTPS port")
    if allowed_hosts is not None and hostname not in allowed_hosts:
        raise UnsafeNetworkTargetError(f"Unexpected HTTPS host: {hostname}")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return normalized
    if not address.is_global:
        raise UnsafeNetworkTargetError(f"Non-public address is not allowed: {hostname}")
    return normalized


def _resolve_public_addresses(hostname: str) -> None:
    addresses = {
        ipaddress.ip_address(sockaddr[0])
        for _family, _socket_type, _protocol, _canonical_name, sockaddr in socket.getaddrinfo(
            hostname,
            443,
            type=socket.SOCK_STREAM,
        )
    }
    if not addresses or any(not address.is_global for address in addresses):
        raise UnsafeNetworkTargetError(f"Host resolves to a non-public address: {hostname}")


async def ensure_public_https_url(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    """Validate syntax and DNS results without blocking the event loop."""
    normalized = validate_public_https_url(url, allowed_hosts=allowed_hosts)
    hostname = urlsplit(normalized).hostname
    if hostname is None:
        raise UnsafeNetworkTargetError("URL hostname is missing")
    await asyncio.to_thread(_resolve_public_addresses, hostname)
    return normalized


def ensure_public_https_url_sync(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    """Validate syntax and DNS results for synchronous HTTP adapters."""
    normalized = validate_public_https_url(url, allowed_hosts=allowed_hosts)
    hostname = urlsplit(normalized).hostname
    if hostname is None:
        raise UnsafeNetworkTargetError("URL hostname is missing")
    _resolve_public_addresses(hostname)
    return normalized


def read_limited_chunks(chunks: Iterable[bytes], maximum_bytes: int) -> bytes:
    """Read synchronous response chunks without exceeding a byte budget."""
    content = bytearray()
    for chunk in chunks:
        content.extend(chunk)
        if len(content) > maximum_bytes:
            raise ValueError(f"Response exceeds {maximum_bytes} bytes")
    return bytes(content)


async def read_limited_async_chunks(chunks: AsyncIterator[bytes], maximum_bytes: int) -> bytes:
    """Read asynchronous response chunks without exceeding a byte budget."""
    content = bytearray()
    async for chunk in chunks:
        content.extend(chunk)
        if len(content) > maximum_bytes:
            raise ValueError(f"Response exceeds {maximum_bytes} bytes")
    return bytes(content)


def ensure_expected_content_type(headers: Mapping[str, str | None], allowed_types: set[str]) -> None:
    """Reject an explicitly supplied response type outside the expected set."""
    content_type = (headers.get("content-type") or "").partition(";")[0].strip().lower()
    if content_type and content_type not in allowed_types:
        raise ValueError(f"Unexpected response content type: {content_type}")
