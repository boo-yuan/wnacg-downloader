from collections.abc import AsyncIterator, Iterator
from typing import ClassVar, cast

import pytest
from curl_cffi.requests import AsyncSession, Response, Session

from wnacg.infrastructure import crawler
from wnacg.infrastructure.crawler import WnacgCrawler


class _Response:
    url = "https://www.wnacg.com/page"
    headers: ClassVar[dict[str, str]] = {"content-type": "text/html", "content-length": "13"}
    encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        del chunk_size
        yield b"<h1>ok</h1>"

    async def aiter_content(self, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        yield b"<h1>ok</h1>"


class _SyncClient:
    def get(self, _url: str, **_kwargs: object) -> _Response:
        return _Response()


class _AsyncClient:
    async def get(self, _url: str, **_kwargs: object) -> _Response:
        return _Response()


def _validate_sync(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    del allowed_hosts
    return url


async def _validate_async(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    del allowed_hosts
    return url


def test_bounded_sync_html_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler, "ensure_public_https_url_sync", _validate_sync)
    client = cast(Session[Response], _SyncClient())

    html, base_url = WnacgCrawler.fetch_text_sync(client, "/page")

    assert html == "<h1>ok</h1>"
    assert base_url == "https://www.wnacg.com"


@pytest.mark.asyncio
async def test_bounded_async_html_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler, "ensure_public_https_url", _validate_async)
    client = cast(AsyncSession[Response], _AsyncClient())

    html, base_url = await WnacgCrawler.fetch_text(client, "/page")

    assert html == "<h1>ok</h1>"
    assert base_url == "https://www.wnacg.com"
