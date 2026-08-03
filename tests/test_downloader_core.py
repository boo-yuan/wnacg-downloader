import asyncio
import threading
import time
from pathlib import Path
from typing import NamedTuple, cast

import pytest

from wnacg.application import download_limits
from wnacg.application.download_limits import (
    AdjustableLimiter,
    DiskSpaceGuard,
    RequestPacer,
    SpeedMonitor,
    TaskByteBudget,
    TokenBucket,
    run_bounded,
)
from wnacg.application.downloader import DownloaderWorker
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import Comic, DownloadTask, TaskStatus


class _ProgressRepository:
    def __init__(self) -> None:
        self.persisted = -1
        self._lock = threading.Lock()

    def update_task_progress(self, _task_id: str, _progress: float, downloaded: int, _total: int) -> None:
        if downloaded == 1:
            time.sleep(0.05)
        with self._lock:
            self.persisted = downloaded


class _DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


@pytest.mark.asyncio
async def test_progress_writes_are_serialized() -> None:
    repository = _ProgressRepository()
    worker = DownloaderWorker(cast(TaskRepository, repository))
    task = DownloadTask(id="task", comic=Comic(aid="1", title="One"), total_images=2)

    async with asyncio.TaskGroup() as task_group:
        task.set_progress(1, 2)
        task_group.create_task(worker._persist_progress(task))
        await asyncio.sleep(0)
        task.set_progress(2, 2)
        task_group.create_task(worker._persist_progress(task))

    assert repository.persisted == 2


@pytest.mark.asyncio
async def test_task_byte_budget_reserves_and_releases() -> None:
    budget = TaskByteBudget(maximum_bytes=10, used_bytes=2)
    await budget.reserve(8)
    assert budget.used_bytes == 10
    with pytest.raises(ValueError, match="byte limit"):
        await budget.reserve(1)
    await budget.release(20)
    assert budget.used_bytes == 0


@pytest.mark.asyncio
async def test_adjustable_limiter_enforces_capacity() -> None:
    limiter = AdjustableLimiter(1)
    active = 0
    peak = 0

    async def enter_slot() -> None:
        nonlocal active, peak
        async with limiter.slot():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    async with asyncio.TaskGroup() as task_group:
        for _index in range(3):
            task_group.create_task(enter_slot())
    assert peak == 1
    await limiter.set_limit(2)


@pytest.mark.asyncio
async def test_speed_monitor_and_unlimited_bucket() -> None:
    monitor = SpeedMonitor()
    await monitor.add(1_024)
    assert await monitor.get_and_reset() > 0
    bucket = TokenBucket()
    await bucket.consume(1_024, 0)
    await bucket.consume(1, 1_000_000)


@pytest.mark.asyncio
async def test_request_pacer_serializes_request_starts() -> None:
    pacer = RequestPacer()
    starts: list[float] = []

    async def record_start() -> None:
        await pacer.wait(0.02)
        starts.append(time.monotonic())

    async with asyncio.TaskGroup() as task_group:
        task_group.create_task(record_start())
        task_group.create_task(record_start())

    assert starts[1] - starts[0] >= 0.01


@pytest.mark.asyncio
async def test_bounded_workers_do_not_create_one_live_task_per_item() -> None:
    active = 0
    peak = 0
    processed = 0

    async def process(_item: int) -> object:
        nonlocal active, peak, processed
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        processed += 1
        active -= 1
        return None

    await run_bounded(list(range(1_000)), process, 8)

    assert processed == 1_000
    assert peak == 8


@pytest.mark.asyncio
async def test_disk_guard_counts_small_writes_across_images(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def low_disk_usage(_directory: str | Path) -> _DiskUsage:
        nonlocal calls
        calls += 1
        return _DiskUsage(total=100, used=95, free=5)

    monkeypatch.setattr(download_limits.shutil, "disk_usage", low_disk_usage)
    guard = DiskSpaceGuard(check_interval_bytes=10)

    await guard.record_write("downloads", 6, minimum_free_bytes=10)
    with pytest.raises(OSError, match="Insufficient free disk space"):
        await guard.record_write("downloads", 5, minimum_free_bytes=10)
    assert calls == 1


def test_destructive_artifact_cleanup_rejects_unrecorded_root(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    task = DownloadTask(
        id="task",
        comic=Comic(aid="1", title="One"),
        save_path=str(tmp_path / "outside"),
        download_root=str(root),
        status=TaskStatus.CANCELED,
    )
    with pytest.raises(ValueError, match="Unsafe task directory"):
        DownloaderWorker._delete_task_artifacts(task, True)
