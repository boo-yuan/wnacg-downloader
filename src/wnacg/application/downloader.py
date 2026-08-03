"""Download scheduling, bounded concurrency, recovery, and file transactions."""

import asyncio
import contextlib
import random
import shutil
import threading
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import TypedDict, cast

from curl_cffi.requests import AsyncSession, Response
from PySide6.QtCore import QObject, QThread, Signal

from wnacg.application.artifacts import reconcile_artifacts
from wnacg.application.download_limits import AdjustableLimiter, SpeedMonitor, TaskByteBudget, TokenBucket
from wnacg.application.file_paths import archive_path, task_directory, validated_task_directory
from wnacg.application.image_files import current_output_files, expected_image_paths, is_valid_image, process_image
from wnacg.application.ports import ImageRecord, TaskRepository
from wnacg.domain.models import Comic, DownloadOptions, DownloadTask, TaskStatus
from wnacg.infrastructure.config import cfg
from wnacg.infrastructure.crawler import WnacgCrawler
from wnacg.infrastructure.logger import logger
from wnacg.infrastructure.network_safety import ensure_expected_content_type, ensure_public_https_url

_IMAGE_CONTENT_TYPES = {
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}


class PendingDeletion(TypedDict):
    """Deferred cleanup for a currently running task."""

    task: DownloadTask
    delete_files: bool


class DownloaderSignals(QObject):
    task_added = Signal(DownloadTask)
    task_progress = Signal(str, int, int)  # task_id, downloaded, total
    task_status_changed = Signal(str, object)  # task_id, TaskStatus enum
    task_error = Signal(str, str)  # task_id, error_message
    badge_update = Signal(int)  # active tasks count
    speed_update = Signal(str)  # speed formatted string
    task_deletion_result = Signal(str, bool, str)  # task_id, succeeded, error


class DownloaderWorker(QThread):
    def __init__(self, repository: TaskRepository) -> None:
        super().__init__()
        self._repository = repository
        self.signals = DownloaderSignals()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active_tasks: dict[str, Future[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._pending_deletions: dict[str, PendingDeletion] = {}
        self._lock = threading.RLock()
        self._connection_limiter: AdjustableLimiter | None = None
        self._speed_monitor = SpeedMonitor()
        self._token_bucket = TokenBucket()
        self._monitor_task: asyncio.Task[None] | None = None
        self._progress_locks: dict[str, asyncio.Lock] = {}
        self._prepared = False
        self._stopping = False

    def prepare(self) -> None:
        """Reconcile persisted state explicitly during application startup."""
        if self._prepared:
            return
        # Reset any leftover DOWNLOADING tasks to PAUSED on startup
        self._repository.reset_downloading_tasks()

        # Check for deleted folders for completed tasks
        tasks = self._repository.get_all_tasks()
        for task in tasks:
            if task.status == TaskStatus.COMPLETED:
                save_path = Path(task.save_path)
                if not save_path.exists() and not archive_path(save_path).exists():
                    self._repository.update_task_status(
                        task.id,
                        TaskStatus.MISSING,
                        "Completed download artifacts are missing",
                    )
        self._prepared = True

    @staticmethod
    def _options_from_config(*, naming_version: int = 2) -> DownloadOptions:
        return DownloadOptions(
            naming=cfg.download_naming,
            image_format=cfg.download_format,
            delay_seconds=cfg.download_delay,
            pack_to_zip=cfg.pack_to_zip,
            delete_original_after_pack=cfg.delete_original_after_pack,
            naming_version=naming_version,
        )

    def add_task(self, comic: Comic) -> DownloadTask:
        existing = self._repository.get_task_by_aid(comic.aid)
        if existing:
            if existing.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING):
                return existing
            elif existing.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.MISSING):
                if cfg.auto_start_download and self._loop:
                    self.resume_task(existing.id)
                return existing
            elif existing.status == TaskStatus.COMPLETED:
                existing.status = TaskStatus.PENDING if cfg.auto_start_download else TaskStatus.PAUSED
                existing.set_progress(0, existing.total_images)
                existing.error_message = ""
                existing.options = self._options_from_config()
                self._repository.save_task(existing)
                self.signals.task_status_changed.emit(existing.id, existing.status)
                self.signals.task_progress.emit(existing.id, 0, existing.total_images)
                if cfg.auto_start_download and self._loop:
                    self.resume_task(existing.id)
                return existing

        task_id = str(uuid.uuid4())
        download_root = Path(cfg.download_dir).expanduser().resolve()
        save_path = task_directory(download_root, comic.title, comic.aid)

        initial_status = TaskStatus.PENDING if cfg.auto_start_download else TaskStatus.PAUSED
        task = DownloadTask(
            id=task_id,
            comic=comic,
            save_path=str(save_path),
            download_root=str(download_root),
            options=self._options_from_config(),
            status=initial_status,
        )
        self._repository.save_task(task)
        self.signals.task_added.emit(task)
        self._update_badge()

        if cfg.auto_start_download and self._loop:
            self.resume_task(task.id)

        return task

    def pause_tasks(self, task_ids: list[str]) -> None:
        loop = self._loop
        with self._lock:
            for task_id in task_ids:
                if task_id in self._cancel_events and loop is not None:
                    loop.call_soon_threadsafe(self._cancel_events[task_id].set)
                self._repository.update_task_status(task_id, TaskStatus.PAUSED)
                self.signals.task_status_changed.emit(task_id, TaskStatus.PAUSED)
        self._update_badge()
        self._check_queue()

    def pause_task(self, task_id: str) -> None:
        self.pause_tasks([task_id])

    def resume_tasks(self, task_ids: list[str]) -> None:
        with self._lock:
            for task_id in task_ids:
                if self._loop:
                    self._repository.update_task_status(task_id, TaskStatus.PENDING)
                    self.signals.task_status_changed.emit(task_id, TaskStatus.PENDING)
        self._update_badge()
        self._check_queue()

    def resume_task(self, task_id: str) -> None:
        self.resume_tasks([task_id])

    def _check_queue(self) -> None:
        if not self._loop or self._stopping:
            return

        with self._lock:
            if len(self._active_tasks) >= cfg.max_concurrent_tasks:
                return

            tasks = self._repository.get_all_tasks()
            pending_tasks = [t for t in tasks if t.status == TaskStatus.PENDING and t.id not in self._active_tasks]

            if pending_tasks:
                logger.info(
                    "Checking queue: {} active, {} pending, max {}",
                    len(self._active_tasks),
                    len(pending_tasks),
                    cfg.max_concurrent_tasks,
                )

            for t in pending_tasks:
                if len(self._active_tasks) >= cfg.max_concurrent_tasks:
                    break
                task_id = t.id
                coro = asyncio.run_coroutine_threadsafe(self._process_task(task_id), self._loop)
                self._active_tasks[task_id] = coro
                logger.info(f"Started task {task_id}, active tasks: {len(self._active_tasks)}")

        self._update_badge()

    def delete_tasks(self, task_ids: list[str], delete_files: bool = False) -> None:
        with self._lock:
            for task_id in task_ids:
                try:
                    task = self._repository.get_task(task_id)
                    if task is None:
                        self.signals.task_deletion_result.emit(task_id, True, "")
                        continue
                    should_delete_files = delete_files and cfg.delete_files_on_cancel
                    if task_id in self._cancel_events:
                        self._pending_deletions[task_id] = PendingDeletion(
                            task=task,
                            delete_files=should_delete_files,
                        )
                        if self._loop is not None:
                            self._loop.call_soon_threadsafe(self._cancel_events[task_id].set)
                        self._repository.update_task_status(task_id, TaskStatus.CANCELED)
                        self.signals.task_status_changed.emit(task_id, TaskStatus.CANCELED)
                    else:
                        self._delete_task_artifacts(task, should_delete_files)
                        self._repository.delete_task(task_id)
                        self.signals.task_deletion_result.emit(task_id, True, "")
                except Exception as error:
                    logger.error("Task deletion failed", task_id=task_id, error=str(error))
                    self.signals.task_deletion_result.emit(task_id, False, str(error))
        self._update_badge()
        self._check_queue()

    @staticmethod
    def _delete_task_artifacts(task: DownloadTask, delete_files: bool) -> None:
        if not delete_files:
            return
        task_path = Path(task.save_path)
        recorded_root = Path(task.download_root or task_path.parent)
        safe_task_path = validated_task_directory(task_path, recorded_root)
        if safe_task_path.exists():
            shutil.rmtree(safe_task_path)
            logger.info("Deleted task directory", path=str(safe_task_path), task_id=task.id)
        zip_path = archive_path(safe_task_path)
        if zip_path.exists():
            zip_path.unlink()
            logger.info("Deleted task archive", path=str(zip_path), task_id=task.id)

    def cancel_tasks(self, task_ids: list[str]) -> None:
        self.delete_tasks(task_ids, delete_files=True)

    def cancel_task(self, task_id: str) -> None:
        self.cancel_tasks([task_id])

    def _update_badge(self) -> None:
        tasks = self._repository.get_all_tasks()
        active_count = sum(1 for t in tasks if t.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING))
        self.signals.badge_update.emit(active_count)

    def apply_runtime_limits(self) -> None:
        """Apply queue and connection settings changed by the UI."""
        self._check_queue()
        if self._loop is not None and self._connection_limiter is not None:
            asyncio.run_coroutine_threadsafe(
                self._connection_limiter.set_limit(cfg.global_max_connections),
                self._loop,
            )

    async def _download_image(
        self,
        *,
        client: AsyncSession[Response],
        task: DownloadTask,
        image: ImageRecord,
        options: DownloadOptions,
        cancel_event: asyncio.Event,
        byte_budget: TaskByteBudget,
    ) -> bool:
        if self._connection_limiter is None:
            raise RuntimeError("Connection limiter is not initialized")
        index = image["image_index"]
        raw_url = image["raw_url"]
        if cancel_event.is_set():
            return False

        if not raw_url:
            for attempt in range(3):
                if cancel_event.is_set():
                    return False
                async with self._connection_limiter.slot():
                    raw_url = await WnacgCrawler.get_raw_image_url(image["view_url"], client)
                if raw_url:
                    await asyncio.to_thread(self._repository.update_image_raw_url, task.id, index, raw_url)
                    break
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
            if not raw_url:
                logger.error("Failed to resolve raw image URL", task_id=task.id, image_index=index)
                return False

        raw_url = await ensure_public_https_url(raw_url)

        resolved_image = ImageRecord(**image)
        resolved_image["raw_url"] = raw_url
        expected_paths = expected_image_paths(task, resolved_image, options)
        existing_image = False
        for path in expected_paths:
            if await asyncio.to_thread(is_valid_image, path, cfg.max_image_pixels):
                existing_image = True
                break
        if existing_image:
            if image["status"] != "downloaded":
                await asyncio.to_thread(self._repository.update_image_status, task.id, index, "downloaded")
                await self._increment_progress(task)
            return True

        maximum_bytes = cfg.max_image_bytes
        temporary_download = Path(task.save_path) / f".{index:04d}.{uuid.uuid4().hex}.download"
        for attempt in range(5):
            reserved_bytes = 0
            if cancel_event.is_set():
                return False
            try:
                async with self._connection_limiter.slot():
                    if options.delay_seconds > 0:
                        jitter = random.uniform(0.7, 1.3)
                        await asyncio.sleep(options.delay_seconds * jitter)
                    response = await client.get(raw_url, timeout=30.0, stream=True)
                    response.raise_for_status()
                    await ensure_public_https_url(str(response.url))
                    ensure_expected_content_type(response.headers, _IMAGE_CONTENT_TYPES)
                    content_length = int(response.headers.get("content-length", "0") or 0)
                    if content_length > maximum_bytes:
                        raise ValueError(f"Image exceeds {maximum_bytes} byte limit")
                    downloaded_bytes = 0
                    with temporary_download.open("wb") as output:
                        chunks = cast(
                            AsyncIterator[bytes],
                            response.aiter_content(chunk_size=64 * 1024),  # pyright: ignore[reportUnknownMemberType]
                        )
                        async for chunk in chunks:
                            if cancel_event.is_set():
                                await byte_budget.release(reserved_bytes)
                                return False
                            downloaded_bytes += len(chunk)
                            if downloaded_bytes > maximum_bytes:
                                raise ValueError(f"Image exceeds {maximum_bytes} byte limit")
                            await byte_budget.reserve(len(chunk))
                            reserved_bytes += len(chunk)
                            if downloaded_bytes % (8 * 1024 * 1024) < len(chunk):
                                free_bytes = shutil.disk_usage(task.save_path).free
                                if free_bytes < cfg.minimum_free_space_bytes:
                                    raise OSError("Insufficient free disk space for download")
                            output.write(chunk)
                            await self._speed_monitor.add(len(chunk))
                            await self._token_bucket.consume(len(chunk), cfg.global_speed_limit * 1024)
                await asyncio.to_thread(
                    process_image,
                    temporary_download,
                    Path(task.save_path),
                    index,
                    raw_url,
                    options,
                    cfg.max_image_pixels,
                )
                await asyncio.to_thread(self._repository.update_image_status, task.id, index, "downloaded")
                await self._increment_progress(task)
                return True
            except Exception as error:
                await byte_budget.release(reserved_bytes)
                if attempt == 4:
                    logger.error(
                        "Image download failed",
                        task_id=task.id,
                        image_index=index,
                        error=str(error),
                    )
                else:
                    await asyncio.sleep(2**attempt)
            finally:
                temporary_download.unlink(missing_ok=True)
        return False

    async def _persist_progress(self, task: DownloadTask) -> None:
        progress_lock = self._progress_locks.setdefault(task.id, asyncio.Lock())
        async with progress_lock:
            downloaded_images = task.downloaded_images
            total_images = task.total_images
            progress = downloaded_images / max(1, total_images)
            await asyncio.to_thread(
                self._repository.update_task_progress,
                task.id,
                progress,
                downloaded_images,
                total_images,
            )
            self.signals.task_progress.emit(task.id, downloaded_images, total_images)

    async def _increment_progress(self, task: DownloadTask) -> None:
        """Increment and persist progress while holding the per-task aggregate lock."""
        progress_lock = self._progress_locks.setdefault(task.id, asyncio.Lock())
        async with progress_lock:
            task.set_progress(task.downloaded_images + 1, task.total_images)
            await asyncio.to_thread(
                self._repository.update_task_progress,
                task.id,
                task.progress,
                task.downloaded_images,
                task.total_images,
            )
            self.signals.task_progress.emit(task.id, task.downloaded_images, task.total_images)

    async def _process_task(self, task_id: str) -> None:
        cancel_event = asyncio.Event()
        try:
            task = await asyncio.to_thread(self._repository.get_task, task_id)
            if task is None:
                return
            if not await asyncio.to_thread(self._repository.claim_pending_task, task_id):
                return
            connection_limiter = self._connection_limiter
            if connection_limiter is None:
                raise RuntimeError("Connection limiter is not initialized")
            if task.options is None:
                task.options = self._options_from_config(naming_version=1)
                task.download_root = task.download_root or str(Path(task.save_path).parent)
                await asyncio.to_thread(self._repository.save_task, task)
            options = task.options

            with self._lock:
                if task_id not in self._active_tasks:
                    return
                self._cancel_events[task_id] = cancel_event

            self.signals.task_status_changed.emit(task_id, TaskStatus.DOWNLOADING)
            Path(task.save_path).mkdir(parents=True, exist_ok=True)

            images = await asyncio.to_thread(self._repository.get_images, task_id)
            if not images:
                expected_count = WnacgCrawler.expected_count(task.comic.pic_count)
                try:
                    raw_urls = await WnacgCrawler.get_all_raw_urls(task.comic.aid, connection_limiter.slot)
                except Exception as error:
                    raw_urls = []
                    logger.warning(
                        "Direct gallery endpoint failed; falling back to paginated crawl",
                        task_id=task_id,
                        error=str(error),
                    )
                raw_links_verified = bool(raw_urls and expected_count is not None and len(raw_urls) == expected_count)
                if raw_links_verified:
                    await asyncio.to_thread(self._repository.save_raw_links, task_id, raw_urls)
                else:
                    if raw_urls:
                        logger.warning(
                            "Gallery direct-link count mismatch; falling back to paginated crawl",
                            task_id=task_id,
                            expected=expected_count,
                            actual=len(raw_urls),
                        )
                    view_links = await WnacgCrawler.get_image_view_links(
                        task.comic.aid,
                        connection_limiter.slot,
                    )
                    if expected_count is not None and len(view_links) != expected_count:
                        raise RuntimeError(
                            f"Incomplete gallery crawl: expected {expected_count} images, found {len(view_links)}"
                        )
                    if raw_urls and len(raw_urls) == len(view_links):
                        await asyncio.to_thread(self._repository.save_raw_links, task_id, raw_urls)
                    else:
                        await asyncio.to_thread(self._repository.save_view_links, task_id, view_links)
                images = await asyncio.to_thread(self._repository.get_images, task_id)

            total_images = len(images)
            if total_images == 0:
                raise RuntimeError("No images found in gallery")
            if total_images > cfg.max_gallery_images:
                raise RuntimeError(f"Gallery exceeds configured image limit: {total_images}")

            downloaded_count = 0
            existing_bytes = 0
            for image in images:
                valid = False
                for path in expected_image_paths(task, image, options):
                    if await asyncio.to_thread(is_valid_image, path, cfg.max_image_pixels):
                        valid = True
                        existing_bytes += path.stat().st_size
                        break
                target_status = "downloaded" if valid else "pending"
                if image["status"] != target_status:
                    await asyncio.to_thread(
                        self._repository.update_image_status,
                        task_id,
                        image["image_index"],
                        target_status,
                    )
                downloaded_count += int(valid)
            task.set_progress(downloaded_count, total_images)
            await self._persist_progress(task)
            if existing_bytes > cfg.max_task_bytes:
                raise RuntimeError("Existing gallery files exceed configured task byte limit")
            if shutil.disk_usage(task.save_path).free < cfg.minimum_free_space_bytes:
                raise OSError("Insufficient free disk space for download")
            byte_budget = TaskByteBudget(cfg.max_task_bytes, existing_bytes)

            if task.downloaded_images < task.total_images:
                images = await asyncio.to_thread(self._repository.get_images, task_id)
                async with WnacgCrawler.get_client() as client:
                    try:
                        async with asyncio.TaskGroup() as task_group:
                            for image in images:
                                if image["status"] != "downloaded":
                                    task_group.create_task(
                                        self._download_image(
                                            client=client,
                                            task=task,
                                            image=image,
                                            options=options,
                                            cancel_event=cancel_event,
                                            byte_budget=byte_budget,
                                        )
                                    )
                    except* Exception as error_group:
                        for error in error_group.exceptions:
                            logger.error("Image task raised", task_id=task_id, error=str(error))
                        raise RuntimeError("One or more image tasks raised") from error_group

            if cancel_event.is_set():
                return
            if task.downloaded_images != task.total_images:
                missing = task.total_images - task.downloaded_images
                raise RuntimeError(f"{missing} images could not be downloaded")
            images = await asyncio.to_thread(self._repository.get_images, task_id)
            current_files = await asyncio.to_thread(
                current_output_files,
                task,
                images,
                options,
                cfg.max_image_pixels,
            )
            await asyncio.to_thread(
                reconcile_artifacts,
                task_id=task.id,
                source_directory=Path(task.save_path),
                current_files=current_files,
                pack_to_zip=options.pack_to_zip,
                delete_originals=options.delete_original_after_pack,
            )
            await asyncio.to_thread(self._repository.update_task_status, task_id, TaskStatus.COMPLETED)
            self.signals.task_status_changed.emit(task_id, TaskStatus.COMPLETED)
        except Exception as error:
            if not cancel_event.is_set():
                logger.error("Download task failed", task_id=task_id, error=str(error))
                await asyncio.to_thread(
                    self._repository.update_task_status,
                    task_id,
                    TaskStatus.FAILED,
                    str(error),
                )
                self.signals.task_error.emit(task_id, str(error))
                self.signals.task_status_changed.emit(task_id, TaskStatus.FAILED)
        finally:
            with self._lock:
                self._cancel_events.pop(task_id, None)
                self._active_tasks.pop(task_id, None)
                self._progress_locks.pop(task_id, None)
                pending_deletion = self._pending_deletions.pop(task_id, None)
            if pending_deletion is not None:
                deletion_succeeded = True
                try:
                    await asyncio.to_thread(
                        self._delete_task_artifacts,
                        pending_deletion["task"],
                        pending_deletion["delete_files"],
                    )
                except Exception as error:
                    deletion_succeeded = False
                    logger.error("Deferred artifact deletion failed", task_id=task_id, error=str(error))
                if deletion_succeeded:
                    try:
                        await asyncio.to_thread(self._repository.delete_task, task_id)
                        self.signals.task_deletion_result.emit(task_id, True, "")
                    except Exception as error:
                        logger.error("Deferred database deletion failed", task_id=task_id, error=str(error))
                        self.signals.task_deletion_result.emit(task_id, False, str(error))
                else:
                    self.signals.task_deletion_result.emit(task_id, False, "Artifact deletion failed")
            self._update_badge()
            self._check_queue()

    async def _monitor_loop(self) -> None:
        while True:
            try:
                speed = await self._speed_monitor.get_and_reset()
                # formatting speed
                if speed < 1024:
                    speed_str = f"{speed:.0f} B/s"
                elif speed < 1024 * 1024:
                    speed_str = f"{speed / 1024:.1f} KB/s"
                else:
                    speed_str = f"{speed / (1024 * 1024):.2f} MB/s"

                # Only emit if there are active tasks
                if self._active_tasks:
                    self.signals.speed_update.emit(speed_str)
                else:
                    self.signals.speed_update.emit("")
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
            await asyncio.sleep(1.0)

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._connection_limiter = AdjustableLimiter(cfg.global_max_connections)

        self._monitor_task = self._loop.create_task(self._monitor_loop())

        # We don't automatically resume PENDING here. UI will trigger resume or we can auto resume.
        # But for robustness, let's auto resume PENDING
        tasks = self._repository.get_all_tasks()
        for task in tasks:
            if task.status == TaskStatus.PENDING:
                self.resume_task(task.id)

        self._loop.run_forever()
        with contextlib.suppress(Exception):
            self._loop.close()

    async def _cancel_loop_tasks(self) -> None:
        """Cancel and drain every task owned by the downloader event loop."""
        current_task = asyncio.current_task()
        tasks = [task for task in asyncio.all_tasks() if task is not current_task]
        for task in tasks:
            task.cancel()
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=8.0)
            if pending:
                logger.warning("Downloader tasks did not stop within timeout", pending=len(pending))

    def stop(self) -> None:
        """Request cancellation, drain the event loop, and join the worker thread."""
        self._stopping = True
        loop = self._loop
        with self._lock:
            for cancel_event in self._cancel_events.values():
                if loop is not None:
                    loop.call_soon_threadsafe(cancel_event.set)

        if loop is not None and loop.is_running():
            shutdown_future = asyncio.run_coroutine_threadsafe(self._cancel_loop_tasks(), loop)
            try:
                shutdown_future.result(timeout=9.0)
            except FutureTimeoutError:
                logger.warning("Downloader event loop cancellation timed out")
            finally:
                loop.call_soon_threadsafe(loop.stop)

        if self.isRunning() and not self.wait(10_000):
            logger.error("Downloader thread did not stop within timeout")
