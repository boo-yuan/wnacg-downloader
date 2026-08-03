from collections.abc import AsyncIterator

import pytest

from wnacg.infrastructure.network_safety import (
    UnsafeNetworkTargetError,
    ensure_expected_content_type,
    ensure_public_https_url,
    ensure_public_https_url_sync,
    read_limited_async_chunks,
    read_limited_chunks,
    validate_public_https_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/image.jpg",
        "https://user:secret@example.com/image.jpg",
        "https://127.0.0.1/image.jpg",
        "https://[::1]/image.jpg",
        "https://example.com:444/image.jpg",
    ],
)
def test_unsafe_network_targets_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeNetworkTargetError):
        validate_public_https_url(url)


def test_https_host_allowlist_is_enforced() -> None:
    assert (
        validate_public_https_url(
            "https://example.com/image.jpg",
            allowed_hosts={"example.com"},
        )
        == "https://example.com/image.jpg"
    )
    with pytest.raises(UnsafeNetworkTargetError, match="Unexpected"):
        validate_public_https_url("https://cdn.example.com/image.jpg", allowed_hosts={"example.com"})


def test_content_type_and_sync_byte_limit() -> None:
    ensure_expected_content_type({"content-type": "image/jpeg; charset=binary"}, {"image/jpeg"})
    assert read_limited_chunks([b"ab", b"cd"], 4) == b"abcd"
    with pytest.raises(ValueError, match="exceeds"):
        read_limited_chunks([b"abc", b"de"], 4)
    with pytest.raises(ValueError, match="Unexpected"):
        ensure_expected_content_type({"content-type": "text/html"}, {"image/jpeg"})


@pytest.mark.asyncio
async def test_async_byte_limit() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"ab"
        yield b"cd"

    assert await read_limited_async_chunks(chunks(), 4) == b"abcd"
    with pytest.raises(ValueError, match="exceeds"):
        await read_limited_async_chunks(chunks(), 3)


def test_synchronous_dns_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    assert ensure_public_https_url_sync("https://example.com/file") == "https://example.com/file"


@pytest.mark.asyncio
async def test_async_dns_rejects_private_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(UnsafeNetworkTargetError, match="non-public"):
        await ensure_public_https_url("https://example.com/file")
