"""Streaming HTTP resources are released on every exit path."""

import asyncio
from typing import cast

import pytest
from curl_cffi.requests import Response

from wnacg.infrastructure.http_streams import (
    close_async_stream_response,
    close_stream_response,
    new_network_event_loop,
)


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
        self.astream_task: asyncio.Future[None] = asyncio.get_running_loop().create_future()
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


def test_windows_network_loop_supports_reader_callbacks() -> None:
    loop = new_network_event_loop()
    try:
        assert hasattr(loop, "add_reader")
    finally:
        loop.close()
