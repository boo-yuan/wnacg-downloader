import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import Future
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from curl_cffi.requests import AsyncSession, Response
from PIL import Image

from wnacg.application.download_limits import AdjustableLimiter, TaskByteBudget
from wnacg.application.downloader import DownloaderWorker
from wnacg.application.file_paths import archive_path
from wnacg.application.ports import ImageRecord
from wnacg.domain.models import Comic, DownloadOptions, DownloadTask, TaskStatus
from wnacg.infrastructure.config import cfg


class MemoryRepository:
    """Small stateful persistence adapter used without patching implementation modules."""

    def __init__(self, tasks: list[DownloadTask] | None = None) -> None:
        self.tasks = {task.id: task for task in tasks or []}
        self.images: dict[str, list[ImageRecord]] = {}
        self.reset_called = False

    def save_task(self, task: DownloadTask) -> None:
        self.tasks[task.id] = task

    def get_all_tasks(self) -> list[DownloadTask]:
        return list(self.tasks.values())

    def get_tasks_page(self, offset: int, limit: int) -> list[DownloadTask]:
        return list(self.tasks.values())[offset : offset + limit]

    def count_tasks(self, statuses: frozenset[TaskStatus] | None = None) -> int:
        if statuses is None:
            return len(self.tasks)
        return sum(task.status in statuses for task in self.tasks.values())

    def get_task(self, task_id: str) -> DownloadTask | None:
        return self.tasks.get(task_id)

    def get_task_by_aid(self, aid: str) -> DownloadTask | None:
        return next((task for task in self.tasks.values() if task.comic.aid == aid), None)

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_message: str | None = None,
    ) -> bool:
        task = self.tasks.get(task_id)
        if task is None:
            return False
        task.status = status
        task.error_message = error_message
        return True

    def claim_pending_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task is None or task.status is not TaskStatus.PENDING:
            return False
        task.status = TaskStatus.DOWNLOADING
        return True

    def update_task_progress(self, task_id: str, progress: float, downloaded: int, total: int) -> None:
        task = self.tasks[task_id]
        task.set_progress(downloaded, total)
        assert task.progress == progress

    def delete_task(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        self.images.pop(task_id, None)

    def reset_downloading_tasks(self) -> None:
        self.reset_called = True
        for task in self.tasks.values():
            if task.status is TaskStatus.DOWNLOADING:
                task.status = TaskStatus.PAUSED

    def save_view_links(self, task_id: str, view_links: list[str]) -> None:
        self.images[task_id] = [
            ImageRecord(
                task_id=task_id,
                image_index=index,
                view_url=url,
                raw_url="",
                status="pending",
                output_name="",
            )
            for index, url in enumerate(view_links)
        ]

    def save_raw_links(self, task_id: str, raw_urls: list[str]) -> None:
        self.images[task_id] = [
            ImageRecord(
                task_id=task_id,
                image_index=index,
                view_url="",
                raw_url=url,
                status="pending",
                output_name="",
            )
            for index, url in enumerate(raw_urls)
        ]

    def get_images(self, task_id: str) -> list[ImageRecord]:
        return self.images.get(task_id, [])

    def update_image_raw_url(self, task_id: str, image_index: int, raw_url: str) -> None:
        self.images[task_id][image_index]["raw_url"] = raw_url

    def update_image_status(self, task_id: str, image_index: int, status: str, output_name: str = "") -> None:
        self.images[task_id][image_index]["status"] = status
        self.images[task_id][image_index]["output_name"] = output_name


class _ImageResponse:
    def __init__(self, payload: bytes) -> None:
        self.url = "https://1.1.1.1/one.jpg"
        self.headers = {"content-type": "image/jpeg", "content-length": str(len(payload))}
        self._payload = payload

    def raise_for_status(self) -> None:
        """Represent a successful response."""

    async def aiter_content(self, chunk_size: int) -> AsyncIterator[bytes]:
        """Yield the payload using the client response protocol."""
        del chunk_size
        yield self._payload


class _ImageClient:
    def __init__(self, payload: bytes) -> None:
        self._response = _ImageResponse(payload)
        self.requested_urls: list[str] = []

    async def get(self, url: str, **request_options: object) -> _ImageResponse:
        del request_options
        self.requested_urls.append(url)
        return self._response


class _ImageClientContext:
    def __init__(self, client: _ImageClient) -> None:
        self._client = client

    async def __aenter__(self) -> _ImageClient:
        return self._client

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_prepare_marks_missing_artifacts_without_deleting_history(tmp_path: Path) -> None:
    task = DownloadTask(
        id="completed",
        comic=Comic(aid="1", title="One"),
        status=TaskStatus.COMPLETED,
        save_path=str(tmp_path / "missing"),
        download_root=str(tmp_path),
    )
    repository = MemoryRepository([task])
    worker = DownloaderWorker(repository)

    worker.prepare()
    worker.prepare()

    assert repository.reset_called
    assert repository.tasks[task.id].status is TaskStatus.MISSING
    assert "missing" in (repository.tasks[task.id].error_message or "").lower()


def test_add_redownload_and_delete_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "download_dir", str(tmp_path))
    monkeypatch.setattr(cfg, "auto_start_download", False)
    repository = MemoryRepository()
    worker = DownloaderWorker(repository)
    comic = Comic(aid="2", title="Two")

    task = worker.add_task(comic)
    assert task.status is TaskStatus.PAUSED
    assert Path(task.save_path).parent == tmp_path
    assert Path(task.save_path).name == "_Two"

    worker._finalize_task_directory(task)
    task.status = TaskStatus.COMPLETED
    task.set_progress(2, 2)
    redownload = worker.add_task(comic)
    assert redownload.id == task.id
    assert redownload.status is TaskStatus.PAUSED
    assert redownload.downloaded_images == 0
    assert Path(redownload.save_path).name == "_Two"

    worker.delete_tasks([task.id], delete_files=False)
    assert repository.get_task(task.id) is None


def test_missing_task_can_be_added_to_paused_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "download_dir", str(tmp_path))
    monkeypatch.setattr(cfg, "auto_start_download", False)
    task = DownloadTask(
        id="missing",
        comic=Comic(aid="missing", title="Missing"),
        status=TaskStatus.MISSING,
        progress=1.0,
        total_images=2,
        downloaded_images=2,
        save_path=str(tmp_path / "Missing"),
        download_root=str(tmp_path),
        error_message="Completed download artifacts are missing",
    )
    repository = MemoryRepository([task])

    queued = DownloaderWorker(repository).add_task(task.comic)

    assert queued.status is TaskStatus.PAUSED
    assert queued.downloaded_images == 0
    assert queued.error_message is None


def test_same_title_tasks_use_numeric_suffix_without_gallery_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cfg, "download_dir", str(tmp_path))
    monkeypatch.setattr(cfg, "auto_start_download", False)
    repository = MemoryRepository()
    worker = DownloaderWorker(repository)

    first = worker.add_task(Comic(aid="100", title="Same title"))
    second = worker.add_task(Comic(aid="200", title="Same title"))

    assert Path(first.save_path).name == "_Same title"
    assert Path(second.save_path).name == "_Same title (2)"
    assert "100" not in Path(first.save_path).name
    assert "200" not in Path(second.save_path).name


def test_prepare_migrates_only_unmaterialized_legacy_paths(tmp_path: Path) -> None:
    repository = MemoryRepository()
    unused = DownloadTask(
        id="unused",
        comic=Comic(aid="100", title="Same title"),
        save_path=str(tmp_path / "Same title [100]"),
        download_root=str(tmp_path),
    )
    materialized = DownloadTask(
        id="materialized",
        comic=Comic(aid="200", title="Same title"),
        save_path=str(tmp_path / "Same title [200]"),
        download_root=str(tmp_path),
    )
    Path(materialized.save_path).mkdir()
    repository.save_task(unused)
    repository.save_task(materialized)

    DownloaderWorker(repository).prepare()

    assert Path(repository.tasks[unused.id].save_path).name == "_Same title"
    assert Path(repository.tasks[materialized.id].save_path).name == "_Same title [200]"
    assert Path(repository.tasks[materialized.id].save_path).is_dir()


def test_prepare_recovers_folder_prefix_from_task_status(tmp_path: Path) -> None:
    interrupted_pending_path = tmp_path / "Pending"
    legacy_pending_path = tmp_path / ".Legacy pending"
    interrupted_completed_path = tmp_path / ".Completed"
    interrupted_pending_path.mkdir()
    legacy_pending_path.mkdir()
    interrupted_completed_path.mkdir()
    pending = DownloadTask(
        id="pending-prefix",
        comic=Comic(aid="pending-prefix", title="Pending"),
        status=TaskStatus.PAUSED,
        save_path=str(tmp_path / ".Pending"),
        download_root=str(tmp_path),
    )
    legacy_pending = DownloadTask(
        id="legacy-pending-prefix",
        comic=Comic(aid="legacy-pending-prefix", title="Legacy pending"),
        status=TaskStatus.PAUSED,
        save_path=str(legacy_pending_path),
        download_root=str(tmp_path),
    )
    completed = DownloadTask(
        id="completed-prefix",
        comic=Comic(aid="completed-prefix", title="Completed"),
        status=TaskStatus.COMPLETED,
        save_path=str(tmp_path / "Completed"),
        download_root=str(tmp_path),
    )
    repository = MemoryRepository([pending, legacy_pending, completed])

    DownloaderWorker(repository).prepare()

    assert Path(repository.tasks[pending.id].save_path).name == "_Pending"
    assert Path(repository.tasks[pending.id].save_path).is_dir()
    assert Path(repository.tasks[legacy_pending.id].save_path).name == "_Legacy pending"
    assert Path(repository.tasks[legacy_pending.id].save_path).is_dir()
    assert not legacy_pending_path.exists()
    assert Path(repository.tasks[completed.id].save_path).name == "Completed"
    assert Path(repository.tasks[completed.id].save_path).is_dir()


def test_pause_resume_and_cancel_commands_update_repository(tmp_path: Path) -> None:
    task = DownloadTask(
        id="commands",
        comic=Comic(aid="3", title="Three"),
        status=TaskStatus.PENDING,
        save_path=str(tmp_path / "Three [3]"),
        download_root=str(tmp_path),
    )
    repository = MemoryRepository([task])
    worker = DownloaderWorker(repository)
    worker.pause_task(task.id)
    assert task.status is TaskStatus.PAUSED

    worker._loop = asyncio.new_event_loop()
    worker._stopping = True
    try:
        worker.resume_task(task.id)
        assert task.status is TaskStatus.PENDING
        worker.cancel_task(task.id)
        assert repository.get_task(task.id) is None
    finally:
        worker._loop.close()


def test_delete_task_preserves_unowned_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "delete_files_on_cancel", True)
    task_path = tmp_path / "Four [4]"
    task_path.mkdir()
    (task_path / "partial.jpg").write_bytes(b"partial")
    zip_path = archive_path(task_path)
    zip_path.write_bytes(b"archive")
    task = DownloadTask(
        id="delete-files",
        comic=Comic(aid="4", title="Four"),
        status=TaskStatus.PAUSED,
        save_path=str(task_path),
        download_root=str(tmp_path),
    )
    repository = MemoryRepository([task])

    DownloaderWorker(repository).delete_tasks([task.id, "already-missing"], delete_files=True)

    assert repository.get_task(task.id) is None
    assert task_path.exists()
    assert (task_path / "partial.jpg").read_bytes() == b"partial"
    assert zip_path.read_bytes() == b"archive"


def test_cancel_completed_task_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "delete_files_on_cancel", True)
    task_path = tmp_path / "Completed [8]"
    task_path.mkdir()
    completed_file = task_path / "0001.jpg"
    completed_file.write_bytes(b"completed")
    task = DownloadTask(
        id="completed-protected",
        comic=Comic(aid="8", title="Completed"),
        status=TaskStatus.COMPLETED,
        save_path=str(task_path),
        download_root=str(tmp_path),
    )
    repository = MemoryRepository([task])

    DownloaderWorker(repository).cancel_task(task.id)

    assert repository.get_task(task.id) is task
    assert completed_file.read_bytes() == b"completed"


@pytest.mark.asyncio
async def test_process_task_completes_from_valid_existing_files(
    tmp_path: Path,
) -> None:
    task_path = tmp_path / "Five [5]"
    task_path.mkdir()
    Image.new("RGB", (8, 8), "blue").save(task_path / "0001-one.jpg")
    task = DownloadTask(
        id="complete-existing",
        comic=Comic(aid="5", title="Five"),
        status=TaskStatus.PENDING,
        save_path=str(task_path),
        download_root=str(tmp_path),
        options=DownloadOptions(naming_version=2),
    )
    repository = MemoryRepository([task])
    repository.images[task.id] = [
        ImageRecord(
            task_id=task.id,
            image_index=0,
            view_url="",
            raw_url="https://img.example/one.png",
            status="pending",
            output_name="",
        )
    ]
    worker = DownloaderWorker(repository)
    worker._connection_limiter = AdjustableLimiter(2)
    worker._active_tasks[task.id] = Future()

    await worker._process_task(task.id)

    assert task.status is TaskStatus.COMPLETED
    assert task.progress == 1.0
    assert repository.images[task.id][0]["status"] == "downloaded"
    assert task.options is not None
    assert task.options.naming_version == 1
    assert Path(task.save_path) == task_path
    assert not (tmp_path / "_Five [5]").exists()
    assert not (task_path / ".wnacg-manifest.json").exists()
    assert (task_path / "one.jpg").exists()
    assert not (task_path / "0001-one.jpg").exists()


@pytest.mark.asyncio
async def test_paused_task_resumes_from_persisted_output_without_redownloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from wnacg.application import downloader
    from wnacg.infrastructure.crawler import WnacgCrawler

    payload_buffer = BytesIO()
    Image.new("RGB", (8, 8), "green").save(payload_buffer, format="JPEG")
    client = _ImageClient(payload_buffer.getvalue())
    monkeypatch.setattr(WnacgCrawler, "get_client", lambda: _ImageClientContext(client))
    monkeypatch.setattr(downloader, "ARTIFACT_METADATA_DIR", tmp_path / "metadata")

    incomplete_path = tmp_path / "_Resume"
    completed_path = tmp_path / "Resume"
    incomplete_path.mkdir()
    Image.new("RGB", (8, 8), "blue").save(incomplete_path / "one.jpg")
    task = DownloadTask(
        id="resume-partial",
        comic=Comic(aid="resume", title="Resume"),
        status=TaskStatus.PENDING,
        progress=0.5,
        total_images=2,
        downloaded_images=1,
        save_path=str(incomplete_path),
        download_root=str(tmp_path),
        options=DownloadOptions(delay_seconds=0.0),
    )
    repository = MemoryRepository([task])
    repository.images[task.id] = [
        ImageRecord(
            task_id=task.id,
            image_index=0,
            view_url="",
            raw_url="https://1.1.1.1/one.jpg",
            status="downloaded",
            output_name="one.jpg",
        ),
        ImageRecord(
            task_id=task.id,
            image_index=1,
            view_url="",
            raw_url="https://1.1.1.1/two.jpg",
            status="pending",
            output_name="",
        ),
    ]
    worker = DownloaderWorker(repository)
    worker._connection_limiter = AdjustableLimiter(2)
    worker._active_tasks[task.id] = Future()

    await worker._process_task(task.id)

    assert client.requested_urls == ["https://1.1.1.1/two.jpg"]
    assert task.status is TaskStatus.COMPLETED
    assert task.downloaded_images == 2
    assert Path(task.save_path) == completed_path
    assert not incomplete_path.exists()
    assert (completed_path / "one.jpg").exists()
    assert (completed_path / "two.jpg").exists()
    assert not (completed_path / ".wnacg-manifest.json").exists()
    assert len(list((tmp_path / "metadata").glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_failed_final_transaction_restores_incomplete_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from wnacg.application import downloader

    incomplete_path = tmp_path / "_Transaction"
    incomplete_path.mkdir()
    Image.new("RGB", (8, 8), "blue").save(incomplete_path / "one.jpg")
    task = DownloadTask(
        id="failed-transaction",
        comic=Comic(aid="failed-transaction", title="Transaction"),
        status=TaskStatus.PENDING,
        save_path=str(incomplete_path),
        download_root=str(tmp_path),
        options=DownloadOptions(),
    )
    repository = MemoryRepository([task])
    repository.images[task.id] = [
        ImageRecord(
            task_id=task.id,
            image_index=0,
            view_url="",
            raw_url="https://1.1.1.1/one.jpg",
            status="downloaded",
            output_name="one.jpg",
        )
    ]

    def fail_reconciliation(**_kwargs: object) -> None:
        raise OSError("simulated final transaction failure")

    monkeypatch.setattr(downloader, "reconcile_artifacts", fail_reconciliation)
    worker = DownloaderWorker(repository)
    worker._connection_limiter = AdjustableLimiter(2)
    worker._active_tasks[task.id] = Future()

    await worker._process_task(task.id)

    assert task.status is TaskStatus.FAILED
    assert Path(task.save_path) == incomplete_path
    assert incomplete_path.is_dir()
    assert not (tmp_path / "Transaction").exists()


@pytest.mark.asyncio
async def test_process_task_rejects_gallery_over_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cfg, "max_gallery_images", 1)
    task = DownloadTask(
        id="too-many",
        comic=Comic(aid="6", title="Six"),
        status=TaskStatus.PENDING,
        save_path=str(tmp_path / "Six [6]"),
        download_root=str(tmp_path),
        options=DownloadOptions(),
    )
    repository = MemoryRepository([task])
    repository.save_raw_links(task.id, ["https://img.example/1.jpg", "https://img.example/2.jpg"])
    worker = DownloaderWorker(repository)
    worker._connection_limiter = AdjustableLimiter(2)
    worker._active_tasks[task.id] = Future()

    await worker._process_task(task.id)

    assert task.status is TaskStatus.FAILED
    assert "configured image limit" in (task.error_message or "")
    assert Path(task.save_path).name == "_Six [6]"


@pytest.mark.asyncio
async def test_download_image_streams_valid_payload_and_persists_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cfg, "download_delay", 0.0)
    payload_buffer = BytesIO()
    Image.new("RGB", (8, 8), "green").save(payload_buffer, format="JPEG")
    payload = payload_buffer.getvalue()
    task = DownloadTask(
        id="download-one",
        comic=Comic(aid="7", title="Seven"),
        save_path=str(tmp_path),
        download_root=str(tmp_path.parent),
        total_images=1,
    )
    image = ImageRecord(
        task_id=task.id,
        image_index=0,
        view_url="",
        raw_url="https://1.1.1.1/one.jpg",
        status="pending",
        output_name="",
    )
    repository = MemoryRepository([task])
    repository.images[task.id] = [image]
    worker = DownloaderWorker(repository)
    worker._connection_limiter = AdjustableLimiter(1)

    succeeded = await worker._download_image(
        client=cast(AsyncSession[Response], _ImageClient(payload)),
        task=task,
        image=image,
        options=DownloadOptions(delay_seconds=0.0),
        cancel_event=asyncio.Event(),
        byte_budget=TaskByteBudget(1_000_000),
    )

    assert succeeded
    assert task.progress == 1.0
    assert repository.images[task.id][0]["status"] == "downloaded"
    assert (tmp_path / "one.jpg").exists()
    assert repository.images[task.id][0]["output_name"] == "one.jpg"


@pytest.mark.asyncio
async def test_raw_url_resolution_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from wnacg.application import downloader
    from wnacg.infrastructure.crawler import WnacgCrawler

    payload_buffer = BytesIO()
    Image.new("RGB", (8, 8), "purple").save(payload_buffer, format="JPEG")
    payload = payload_buffer.getvalue()
    attempts = 0

    async def resolve_raw_url(_view_url: str, _client: AsyncSession[Response] | None = None) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary DNS failure")
        return "https://1.1.1.1/retried.jpg"

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(WnacgCrawler, "get_raw_image_url", resolve_raw_url)
    monkeypatch.setattr(downloader.asyncio, "sleep", no_delay)
    task = DownloadTask(
        id="retry-resolution",
        comic=Comic(aid="9", title="Retry"),
        save_path=str(tmp_path),
        download_root=str(tmp_path.parent),
        total_images=1,
    )
    image = ImageRecord(
        task_id=task.id,
        image_index=0,
        view_url="https://www.wnacg.com/view-1",
        raw_url="",
        status="pending",
        output_name="",
    )
    repository = MemoryRepository([task])
    repository.images[task.id] = [image]
    worker = DownloaderWorker(repository)
    worker._connection_limiter = AdjustableLimiter(1)

    succeeded = await worker._download_image(
        client=cast(AsyncSession[Response], _ImageClient(payload)),
        task=task,
        image=image,
        options=DownloadOptions(delay_seconds=0.0),
        cancel_event=asyncio.Event(),
        byte_budget=TaskByteBudget(1_000_000),
    )

    assert succeeded
    assert attempts == 2


@pytest.mark.asyncio
async def test_processed_output_cannot_exceed_task_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from wnacg.application import downloader

    payload_buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(payload_buffer, format="PNG")
    payload = payload_buffer.getvalue()

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(downloader.asyncio, "sleep", no_delay)
    task = DownloadTask(
        id="processed-budget",
        comic=Comic(aid="10", title="Budget"),
        save_path=str(tmp_path),
        download_root=str(tmp_path.parent),
        total_images=1,
    )
    image = ImageRecord(
        task_id=task.id,
        image_index=0,
        view_url="",
        raw_url="https://1.1.1.1/one.jpg",
        status="pending",
        output_name="",
    )
    repository = MemoryRepository([task])
    repository.images[task.id] = [image]
    worker = DownloaderWorker(repository)
    worker._connection_limiter = AdjustableLimiter(1)
    budget = TaskByteBudget(len(payload))

    succeeded = await worker._download_image(
        client=cast(AsyncSession[Response], _ImageClient(payload)),
        task=task,
        image=image,
        options=DownloadOptions(delay_seconds=0.0),
        cancel_event=asyncio.Event(),
        byte_budget=budget,
    )

    assert not succeeded
    assert budget.used_bytes == 0
    assert not (tmp_path / "one.jpg").exists()
