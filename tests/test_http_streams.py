"""Streaming HTTP resources are released on every exit path."""

from typing import cast

import pytest
from curl_cffi.requests import Response

from wnacg.infrastructure.http_streams import close_async_stream_response, close_stream_response


class QuitEvent:
    def __init__(self) -> None:
        self.was_set = False

    def set(self) -> None:
        self.was_set = True


class SyncResponse:
    def __init__(self) -> None:
        self.was_closed = False

    def close(self) -> None:
        self.was_closed = True


class AsyncResponse:
    def __init__(self) -> None:
        self.quit_now = QuitEvent()
        self.was_closed = False

    async def aclose(self) -> None:
        self.was_closed = True


def test_sync_stream_close_is_deterministic() -> None:
    fake = SyncResponse()

    close_stream_response(cast(Response, fake))

    assert fake.was_closed


@pytest.mark.asyncio
async def test_async_stream_is_aborted_before_join() -> None:
    fake = AsyncResponse()

    await close_async_stream_response(cast(Response, fake))

    assert fake.quit_now.was_set
    assert fake.was_closed
