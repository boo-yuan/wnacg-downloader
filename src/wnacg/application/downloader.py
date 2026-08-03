"""Download scheduling, bounded concurrency, recovery, and file transactions."""

import asyncio
import contextlib
import random
import shutil
import threading
import time
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import TypedDict, cast

from curl_cffi.requests import AsyncSession, Response
from PySide6.QtCore import QObject, QThread, Signal

from wnacg.application.artifacts import reconcile_artifacts, remove_owned_artifacts
from wnacg.application.download_limits import (
    AdjustableLimiter,
    DiskSpaceGuard,
    RequestPacer,
    SpeedMonitor,
    TaskByteBudget,
    TokenBucket,
    run_bounded,
)
from wnacg.application.file_paths import (
    archive_path,
    prepare_task_directory,
    safe_component,
    task_directory,
    validated_task_directory,
)
from wnacg.application.image_files import current_output_files, expected_image_paths, is_valid_image, process_image
from wnacg.application.ports import ImageRecord, TaskRepository
from wnacg.domain.models import CANCELLABLE_TASK_STATUSES, Comic, DownloadOptions, DownloadTask, TaskStatus
from wnacg.infrastructure.config import ProxyMode, cfg
from wnacg.infrastructure.crawler import WnacgCrawler
from wnacg.infrastructure.http_streams import close_async_stream_response, new_network_event_loop
from wnacg.infrastructure.logger import logger
from wnacg.infrastructure.network_safety import (
    ensure_expected_content_type,
    ensure_public_https_url,
    ensure_public_peer_address,
)

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
    expected_files: list[Path]


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
        self._request_pacer = RequestPacer()
        self._monitor_task: asyncio.Task[None] | None = None
        self._progress_locks: dict[str, asyncio.Lock] = {}
        self._reserved_task_paths: set[str] = set()
        self._task_paths_initialized = False
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
        self._migrate_unused_legacy_task_paths(tasks)
        self._initialize_task_paths(tasks)
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

    @staticmethod
    def _task_path_key(path: Path) -> str:
        """Normalize a path for case-insensitive reservation checks on Windows."""
        return str(path.resolve(strict=False)).casefold()

    def _initialize_task_paths(self, tasks: list[DownloadTask] | None = None) -> None:
        """Reserve persisted paths once so title collisions never merge task output."""
        with self._lock:
            if self._task_paths_initialized:
                return
            known_tasks = tasks if tasks is not None else self._repository.get_all_tasks()
            self._reserved_task_paths.update(
                self._task_path_key(Path(task.save_path)) for task in known_tasks if task.save_path
            )
            self._task_paths_initialized = True

    def _migrate_unused_legacy_task_paths(self, tasks: list[DownloadTask]) -> None:
        """Move only unmaterialized legacy ``title [aid]`` records to title-only paths."""
        migration_candidates: list[tuple[DownloadTask, Path, Path]] = []
        occupied_paths: set[str] = set()
        for task in tasks:
            old_path = Path(task.save_path)
            download_root = Path(task.download_root or old_path.parent).expanduser().resolve()
            safe_aid = safe_component(task.comic.aid, "unknown")
            safe_title = safe_component(task.comic.title, safe_aid)
            expected_legacy_path = download_root / f"{safe_title} [{safe_aid}]"
            legacy_matches = self._task_path_key(old_path) == self._task_path_key(expected_legacy_path)
            old_archive = archive_path(old_path)
            has_artifacts = (
                old_path.exists() or old_path.is_symlink() or old_archive.exists() or old_archive.is_symlink()
            )
            if legacy_matches and not has_artifacts:
                migration_candidates.append((task, old_path, download_root))
            else:
                occupied_paths.add(self._task_path_key(old_path))

        for task, old_path, download_root in migration_candidates:
            base_path = task_directory(download_root, task.comic.title)
            candidate = base_path
            suffix = 2
            while (
                self._task_path_key(candidate) in occupied_paths
                or candidate.exists()
                or candidate.is_symlink()
                or archive_path(candidate).exists()
                or archive_path(candidate).is_symlink()
            ):
                candidate = base_path.with_name(f"{base_path.name} ({suffix})")
                suffix += 1
            previous_path = task.save_path
            task.save_path = str(candidate)
            task.download_root = str(download_root)
            try:
                self._repository.save_task(task)
            except Exception as error:
                task.save_path = previous_path
                occupied_paths.add(self._task_path_key(old_path))
                logger.warning("Legacy task path migration failed", task_id=task.id, error=str(error))
                continue
            occupied_paths.add(self._task_path_key(candidate))
            logger.info("Migrated unused legacy task path", task_id=task.id)

    def _reserve_task_directory(self, download_root: Path, title: str) -> Path:
        """Reserve a title-only path, adding a numeric suffix for genuine collisions."""
        self._initialize_task_paths()
        base_path = task_directory(download_root, title)
        candidate = base_path
        suffix = 2
        with self._lock:
            while (
                self._task_path_key(candidate) in self._reserved_task_paths
                or candidate.exists()
                or archive_path(candidate).exists()
            ):
                candidate = base_path.with_name(f"{base_path.name} ({suffix})")
                suffix += 1
            self._reserved_task_paths.add(self._task_path_key(candidate))
        return candidate

    def add_task(self, comic: Comic) -> DownloadTask:
        existing = self._repository.get_task_by_aid(comic.aid)
        if existing:
            if existing.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING):
                return existing
            elif existing.status == TaskStatus.PAUSED:
                if cfg.auto_start_download and self._loop:
                    self.resume_task(existing.id)
                return existing
            elif existing.status in (TaskStatus.FAILED, TaskStatus.MISSING, TaskStatus.COMPLETED):
                previous_status = existing.status
                self._repository.update_task_status(existing.id, TaskStatus.PENDING)
                existing.status = TaskStatus.PENDING
                if previous_status in (TaskStatus.MISSING, TaskStatus.COMPLETED):
                    existing.set_progress(0, existing.total_images)
                    existing.options = self._options_from_config()
                existing.error_message = None
                self._repository.save_task(existing)
                if not cfg.auto_start_download:
                    self._repository.update_task_status(existing.id, TaskStatus.PAUSED)
                    existing.status = TaskStatus.PAUSED
                self.signals.task_status_changed.emit(existing.id, existing.status)
                self.signals.task_progress.emit(existing.id, existing.downloaded_images, existing.total_images)
                if cfg.auto_start_download:
                    self._check_queue()
                return existing

        task_id = str(uuid.uuid4())
        download_root = Path(cfg.download_dir).expanduser().resolve()
        save_path = self._reserve_task_directory(download_root, comic.title)

        initial_status = TaskStatus.PENDING if cfg.auto_start_download else TaskStatus.PAUSED
        task = DownloadTask(
            id=task_id,
            comic=comic,
            save_path=str(save_path),
            download_root=str(download_root),
            options=self._options_from_config(),
            status=initial_status,
        )
        try:
            self._repository.save_task(task)
        except Exception:
            with self._lock:
                self._reserved_task_paths.discard(self._task_path_key(save_path))
            raise
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
                # Holding ``_lock`` through registration ensures _process_task cannot
                # pass its matching active-task check before this entry is visible.
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
                    if delete_files and task.status is TaskStatus.COMPLETED:
                        self.signals.task_deletion_result.emit(
                            task_id,
                            False,
                            "Completed task files must be removed through an explicit completed-artifact action",
                        )
                        continue
                    should_delete_files = delete_files and cfg.delete_files_on_cancel
                    expected_files = self._expected_task_files(task)
                    if task_id in self._cancel_events:
                        self._pending_deletions[task_id] = PendingDeletion(
                            task=task,
                            delete_files=should_delete_files,
                            expected_files=expected_files,
                        )
                        if self._loop is not None:
                            self._loop.call_soon_threadsafe(self._cancel_events[task_id].set)
                        self._repository.update_task_status(task_id, TaskStatus.CANCELED)
                        self.signals.task_status_changed.emit(task_id, TaskStatus.CANCELED)
                    else:
                        self._delete_task_artifacts(task, should_delete_files, expected_files)
                        self._repository.delete_task(task_id)
                        self.signals.task_deletion_result.emit(task_id, True, "")
                except Exception as error:
                    logger.error("Task deletion failed", task_id=task_id, error=str(error))
                    self.signals.task_deletion_result.emit(task_id, False, str(error))
        self._update_badge()
        self._check_queue()

    def _expected_task_files(self, task: DownloadTask) -> list[Path]:
        options = task.options or self._options_from_config(naming_version=1)
        expected: list[Path] = []
        for image in self._repository.get_images(task.id):
            expected.extend(expected_image_paths(task, image, options))
        return expected

    @staticmethod
    def _delete_task_artifacts(
        task: DownloadTask,
        delete_files: bool,
        expected_files: list[Path] | None = None,
    ) -> None:
        if not delete_files:
            return
        task_path = Path(task.save_path)
        recorded_root = Path(task.download_root or task_path.parent)
        safe_task_path = validated_task_directory(task_path, recorded_root)
        if safe_task_path.exists():
            remove_owned_artifacts(
                task_id=task.id,
                source_directory=safe_task_path,
                expected_files=expected_files or [],
            )
            logger.info("Deleted task-owned files", path=str(safe_task_path), task_id=task.id)
        zip_path = archive_path(safe_task_path)
        if zip_path.is_symlink():
            raise ValueError(f"Unsafe task archive is a filesystem link: {zip_path}")
        if zip_path.is_file():
            zip_path.unlink()
            logger.info("Deleted task archive", path=str(zip_path), task_id=task.id)

    def cancel_tasks(self, task_ids: list[str]) -> None:
        cancellable_ids: list[str] = []
        for task_id in task_ids:
            task = self._repository.get_task(task_id)
            if task is None or task.status in CANCELLABLE_TASK_STATUSES:
                cancellable_ids.append(task_id)
                continue
            logger.warning(
                "Ignoring cancellation for task in protected state",
                task_id=task_id,
                status=task.status.value,
            )
            self.signals.task_deletion_result.emit(
                task_id,
                False,
                f"Task in {task.status.value} state cannot be canceled",
            )
        if cancellable_ids:
            self.delete_tasks(cancellable_ids, delete_files=True)

    def cancel_task(self, task_id: str) -> None:
        self.cancel_tasks([task_id])

    def _update_badge(self) -> None:
        active_count = self._repository.count_tasks(frozenset({TaskStatus.PENDING, TaskStatus.DOWNLOADING}))
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
        disk_guard: DiskSpaceGuard | None = None,
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
                try:
                    async with self._connection_limiter.slot():
                        raw_url = await WnacgCrawler.get_raw_image_url(image["view_url"], client)
                except Exception as error:
                    raw_url = ""
                    logger.warning(
                        "Raw image URL resolution failed",
                        task_id=task.id,
                        image_index=index,
                        attempt=attempt + 1,
                        error=str(error),
                    )
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
        safe_task_path = await asyncio.to_thread(
            validated_task_directory,
            Path(task.save_path),
            Path(task.download_root or Path(task.save_path).parent),
        )
        temporary_download = safe_task_path / f".{index:04d}.{uuid.uuid4().hex}.download"
        active_disk_guard = disk_guard or DiskSpaceGuard()
        for attempt in range(5):
            reserved_bytes = 0
            published_output: Path | None = None
            if cancel_event.is_set():
                return False
            try:
                if options.delay_seconds > 0:
                    jitter = random.uniform(0.7, 1.3)
                    await self._request_pacer.wait(options.delay_seconds * jitter)
                async with self._connection_limiter.slot():
                    response = await client.get(raw_url, timeout=30.0, stream=True)
                    try:
                        if cfg.proxy_mode is ProxyMode.DIRECT:
                            ensure_public_peer_address(response.primary_ip)
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
                                await active_disk_guard.record_write(
                                    task.save_path,
                                    len(chunk),
                                    cfg.minimum_free_space_bytes,
                                )
                                output.write(chunk)
                                await self._speed_monitor.add(len(chunk))
                                await self._token_bucket.consume(len(chunk), cfg.global_speed_limit * 1024)
                    finally:
                        await close_async_stream_response(response)
                published_output = await asyncio.to_thread(
                    process_image,
                    temporary_download,
                    Path(task.save_path),
                    index,
                    raw_url,
                    options,
                    cfg.max_image_pixels,
                )
                output_bytes = published_output.stat().st_size
                await active_disk_guard.record_write(
                    task.save_path,
                    output_bytes,
                    cfg.minimum_free_space_bytes,
                )
                if output_bytes > reserved_bytes:
                    additional_bytes = output_bytes - reserved_bytes
                    await byte_budget.reserve(additional_bytes)
                    reserved_bytes += additional_bytes
                elif output_bytes < reserved_bytes:
                    released_bytes = reserved_bytes - output_bytes
                    await byte_budget.release(released_bytes)
                    reserved_bytes = output_bytes
                await asyncio.to_thread(self._repository.update_image_status, task.id, index, "downloaded")
                await self._increment_progress(task)
                return True
            except Exception as error:
                await byte_budget.release(reserved_bytes)
                if published_output is not None:
                    published_output.unlink(missing_ok=True)
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
            recorded_root = Path(task.download_root or Path(task.save_path).parent)
            safe_task_path = await asyncio.to_thread(
                prepare_task_directory,
                Path(task.save_path),
                recorded_root,
            )
            if str(safe_task_path) != task.save_path or task.download_root != str(recorded_root.resolve(strict=False)):
                task.save_path = str(safe_task_path)
                task.download_root = str(recorded_root.resolve(strict=False))
                await asyncio.to_thread(self._repository.save_task, task)

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
            disk_guard = DiskSpaceGuard()

            if task.downloaded_images < task.total_images:
                images = await asyncio.to_thread(self._repository.get_images, task_id)
                async with WnacgCrawler.get_client() as client:
                    pending_images = [image for image in images if image["status"] != "downloaded"]

                    async def download_one(image: ImageRecord) -> object:
                        return await self._download_image(
                            client=client,
                            task=task,
                            image=image,
                            options=options,
                            cancel_event=cancel_event,
                            byte_budget=byte_budget,
                            disk_guard=disk_guard,
                        )

                    try:
                        await run_bounded(pending_images, download_one, cfg.global_max_connections)
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
                        pending_deletion["expected_files"],
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
        self._loop = new_network_event_loop()
        asyncio.set_event_loop(self._loop)
        self._connection_limiter = AdjustableLimiter(cfg.global_max_connections)

        self._monitor_task = self._loop.create_task(self._monitor_loop())

        # Pending tasks are already persisted as runnable; schedule only the available slots once.
        self._check_queue()

        try:
            self._loop.run_forever()
        finally:
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._cancel_loop_tasks())
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_default_executor())
            self._loop.close()
            self._loop = None

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

    def stop(self, deadline: float | None = None) -> None:
        """Request cancellation, drain the event loop, and join the worker thread."""
        shutdown_deadline = deadline or (time.monotonic() + 20.0)
        self._stopping = True
        loop = self._loop
        with self._lock:
            for cancel_event in self._cancel_events.values():
                if loop is not None:
                    loop.call_soon_threadsafe(cancel_event.set)

        if loop is not None and loop.is_running():
            shutdown_future = asyncio.run_coroutine_threadsafe(self._cancel_loop_tasks(), loop)
            try:
                remaining_seconds = max(0.1, min(9.0, shutdown_deadline - time.monotonic()))
                shutdown_future.result(timeout=remaining_seconds)
            except FutureTimeoutError:
                logger.warning("Downloader event loop cancellation timed out")
            finally:
                loop.call_soon_threadsafe(loop.stop)

        remaining_milliseconds = max(0, int((shutdown_deadline - time.monotonic()) * 1_000))
        if self.isRunning() and not self.wait(remaining_milliseconds):
            logger.error("Downloader thread did not stop within timeout")
            self.wait()
