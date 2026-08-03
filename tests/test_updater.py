from collections.abc import AsyncIterator

import pytest

from wnacg.infrastructure import updater


class _FakeResponse:
    def __init__(self, html_url: str) -> None:
        self.text = ('{"tag_name":"v9.0.0","body":"notes","html_url":"' + html_url.replace("/", "\\/") + '"}').replace(
            "\\/", "/"
        )
        self.url = updater.Updater.API_URL
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        return None

    async def aiter_content(self, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        yield self.text.encode()


class _FakeSession:
    response_url = "https://github.com/boo-yuan/wnacg-downloader/releases/tag/v9.0.0"

    def __class_getitem__(cls, _item: object) -> type["_FakeSession"]:
        return cls

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse(self.response_url)


@pytest.mark.asyncio
async def test_updater_only_returns_official_release_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "AsyncSession", _FakeSession)
    monkeypatch.setattr(updater.Updater, "current_version", staticmethod(lambda: "1.0.0"))
    result = await updater.Updater.check_update()
    assert result.has_update
    assert result.download_url.startswith("https://github.com/boo-yuan/wnacg-downloader/releases/")


@pytest.mark.asyncio
async def test_updater_rejects_untrusted_release_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "AsyncSession", _FakeSession)
    monkeypatch.setattr(updater.Updater, "current_version", staticmethod(lambda: "1.0.0"))
    _FakeSession.response_url = "https://attacker.example/download.exe"
    try:
        with pytest.raises(updater.UpdateCheckError):
            await updater.Updater.check_update()
    finally:
        _FakeSession.response_url = "https://github.com/boo-yuan/wnacg-downloader/releases/tag/v9.0.0"
