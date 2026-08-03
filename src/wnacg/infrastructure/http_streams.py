"""Deterministic cleanup for curl-cffi streaming responses."""

import asyncio
from typing import cast

from curl_cffi.requests import Response

from wnacg.infrastructure.logger import logger


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
        if quit_event is not None:
            quit_event.set()
        await response.aclose()
    except Exception as error:
        logger.warning("Failed to close asynchronous HTTP stream", error=str(error))
