"""Regressions for defensive Qt worker shutdown."""

import time
from typing import cast

from PySide6.QtCore import QThread

from wnacg.ui.worker_lifecycle import stop_qthread


class DeletedWorker:
    def requestInterruption(self) -> None:
        raise RuntimeError("Internal C++ object already deleted")


def test_stop_qthread_tolerates_deleted_cpp_object() -> None:
    worker = cast(QThread, DeletedWorker())

    assert stop_qthread(worker, time.monotonic() + 1.0, name="deleted")
