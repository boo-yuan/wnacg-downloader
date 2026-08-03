"""Defensive Qt worker shutdown helpers."""

import time

from PySide6.QtCore import QThread

from wnacg.infrastructure.logger import logger


def stop_qthread(
    worker: QThread | None,
    deadline: float,
    *,
    name: str,
    join_after_timeout: bool = False,
) -> bool:
    """Stop a live QThread without dereferencing an already-deleted C++ object."""
    if worker is None:
        return True
    try:
        worker.requestInterruption()
        if not worker.isRunning():
            return True
        remaining_milliseconds = max(0, int((deadline - time.monotonic()) * 1_000))
        if worker.wait(remaining_milliseconds):
            return True
        logger.warning("Qt worker did not stop within deadline", worker=name)
        if join_after_timeout:
            worker.wait()
            return True
        return False
    except RuntimeError:
        logger.debug("Qt worker C++ object was already deleted", worker=name)
        return True
