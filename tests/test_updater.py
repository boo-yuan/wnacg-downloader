from typing import Any

import pytest

from wnacg.infrastructure import updater


class _FakeResponse:
    def __init__(self, html_url: str) -> None:
        self.text = ('{"tag_name":"v9.0.0","body":"notes","html_url":"' + html_url.replace("/", "\\/") + '"}').replace(
            "\\/", "/"
        )

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    response_url = "https://github.com/boo-yuan/wnacg-downloader/releases/tag/v9.0.0"

    def __class_getitem__(cls, _item: object) -> type["_FakeSession"]:
        return cls

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self.response_url)


@pytest.mark.asyncio
async def test_updater_only_returns_official_release_page(monkeypatch) -> None:
    monkeypatch.setattr(updater, "AsyncSession", _FakeSession)
    monkeypatch.setattr(updater.Updater, "current_version", staticmethod(lambda: "1.0.0"))
    result = await updater.Updater.check_update()
    assert result.has_update
    assert result.download_url.startswith("https://github.com/boo-yuan/wnacg-downloader/releases/")


@pytest.mark.asyncio
async def test_updater_rejects_untrusted_release_url(monkeypatch) -> None:
    monkeypatch.setattr(updater, "AsyncSession", _FakeSession)
    monkeypatch.setattr(updater.Updater, "current_version", staticmethod(lambda: "1.0.0"))
    _FakeSession.response_url = "https://attacker.example/download.exe"
    try:
        with pytest.raises(updater.UpdateCheckError):
            await updater.Updater.check_update()
    finally:
        _FakeSession.response_url = "https://github.com/boo-yuan/wnacg-downloader/releases/tag/v9.0.0"
