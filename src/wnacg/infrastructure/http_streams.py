"""Deterministic cleanup for curl-cffi streaming responses."""

import asyncio
import sys
from typing import cast

from curl_cffi.requests import Response

from wnacg.infrastructure.logger import logger


def new_network_event_loop() -> asyncio.AbstractEventLoop:
    """Use a native selector loop on Windows for curl-cffi socket callbacks."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def close_stream_response(response: Response) -> None:
    """Release a synchronous stream, including partially consumed responses."""
    try:
        response.close()
    except Exception as error:
        logger.warning("Failed to close HTTP stream", error=str(error))


async def close_async_stream_response(response: Response) -> None:
    """Abort and join an asynchronous stream so its curl handle can be reused."""
    try:
        quit_event = cast(asyncio.Event | None, response.quit_now)
        stream_task = cast(
            asyncio.Future[object] | None,
            response.astream_task,  # pyright: ignore[reportUnknownMemberType]
        )
        if quit_event is not None and stream_task is not None and not stream_task.done():
            quit_event.set()
        await response.aclose()
    except Exception as error:
        logger.warning("Failed to close asynchronous HTTP stream", error=str(error))
