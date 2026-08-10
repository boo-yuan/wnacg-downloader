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

from wnacg.application.artifacts import forget_manifest, migrate_manifest, reconcile_artifacts, remove_owned_artifacts
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
    completed_task_directory,
    incomplete_task_directory,
    prepare_task_directory,
    safe_component,
    task_directory,
    validated_task_directory,
)
from wnacg.application.image_files import (
    current_output_files,
    expected_image_paths,
    is_valid_image,
    preferred_image_paths,
    process_image,
)
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
from wnacg.infrastructure.paths import ARTIFACT_METADATA_DIR

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
        self._output_locks: dict[str, asyncio.Lock] = {}
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
        for task in tasks:
            if not task.save_path:
                logger.warning("Skipping task with an empty persisted path", task_id=task.id)
                continue
            try:
                migrate_manifest(Path(task.save_path), task.id, ARTIFACT_METADATA_DIR)
            except OSError as error:
                logger.warning("Legacy artifact metadata migration failed", task_id=task.id, error=str(error))
        self._migrate_unused_legacy_task_paths(tasks)
        self._normalize_persisted_task_paths(tasks)
        self._initialize_task_paths(tasks)
        for task in tasks:
            if task.status == TaskStatus.COMPLETED and task.save_path:
                save_path = Path(task.save_path)
                if not save_path.exists() and not archive_path(save_path).exists():
                    self._repository.update_task_status(
                        task.id,
                        TaskStatus.MISSING,
                        "Completed download artifacts are missing",
                    )
        self._prepared = True

    @staticmethod
    def _options_from_config(*, naming_version: int = 1) -> DownloadOptions:
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

    @staticmethod
    def _leading_character_count(name: str, character: str) -> int:
        return len(name) - len(name.lstrip(character))

    def _task_path_pair(self, task: DownloadTask) -> tuple[Path, Path]:
        """Return the completed and incomplete names for a persisted task path."""
        if not task.save_path:
            raise ValueError(f"Task has no persisted download path: {task.id}")
        current = Path(task.save_path)
        download_root = Path(task.download_root or current.parent)
        base_name = task_directory(download_root, task.comic.title).name
        is_incomplete = self._leading_character_count(current.name, "_") > self._leading_character_count(base_name, "_")
        if is_incomplete:
            return completed_task_directory(current), current
        uses_legacy_dot_prefix = self._leading_character_count(current.name, ".") > self._leading_character_count(
            base_name, "."
        )
        if uses_legacy_dot_prefix:
            completed_path = current.with_name(current.name[1:])
            return completed_path, incomplete_task_directory(completed_path)
        return current, incomplete_task_directory(current)

    @staticmethod
    def _legacy_incomplete_task_directory(completed_directory: Path) -> Path:
        """Return the short-lived dot-prefixed name used by an earlier build."""
        return completed_directory.with_name(f".{completed_directory.name}")

    @staticmethod
    def _path_or_archive_exists(path: Path) -> bool:
        archive = archive_path(path)
        return (
            path.exists()
            or path.is_symlink()
            or path.is_junction()
            or archive.exists()
            or archive.is_symlink()
            or archive.is_junction()
        )

    def _move_task_directory(
        self,
        task: DownloadTask,
        target_path: Path,
        *,
        move_archive: bool,
        source_override: Path | None = None,
    ) -> None:
        """Move one direct-child task directory and persist its path without merging data."""
        source_path = source_override or Path(task.save_path)
        download_root = Path(task.download_root or source_path.parent).expanduser().resolve(strict=False)
        safe_source = validated_task_directory(source_path, download_root)
        safe_target = validated_task_directory(target_path, download_root)
        if self._task_path_key(safe_source) == self._task_path_key(safe_target):
            return

        source_exists = safe_source.exists()
        target_exists = safe_target.exists()
        if source_exists and not safe_source.is_dir():
            raise ValueError(f"Task directory path is not a directory: {safe_source}")
        if target_exists and not safe_target.is_dir():
            raise ValueError(f"Target task directory path is not a directory: {safe_target}")
        source_archive = archive_path(safe_source)
        target_archive = archive_path(safe_target)
        source_archive_exists = move_archive and source_archive.exists()
        if source_exists and target_exists:
            raise FileExistsError(f"Both task directories exist; refusing to merge: {safe_target}")
        if source_archive_exists and target_archive.exists():
            raise FileExistsError(f"Both task archives exist; refusing to overwrite: {target_archive}")

        moved_directory = False
        moved_archive = False
        previous_path = task.save_path
        previous_root = task.download_root
        try:
            if source_exists:
                safe_source.rename(safe_target)
                moved_directory = True
            if source_archive_exists:
                source_archive.rename(target_archive)
                moved_archive = True
            task.save_path = str(safe_target)
            task.download_root = str(download_root)
            self._repository.save_task(task)
        except Exception:
            if moved_archive and target_archive.exists() and not source_archive.exists():
                target_archive.rename(source_archive)
            if moved_directory and safe_target.exists() and not safe_source.exists():
                safe_target.rename(safe_source)
            task.save_path = previous_path
            task.download_root = previous_root
            raise

    def _ensure_incomplete_task_directory(self, task: DownloadTask) -> None:
        """Ensure a queued, paused, or failed task uses an underscore-prefixed directory."""
        _completed_path, incomplete_path = self._task_path_pair(task)
        if self._task_path_key(Path(task.save_path)) == self._task_path_key(incomplete_path):
            return
        self._move_task_directory(task, incomplete_path, move_archive=False)

    def _finalize_task_directory(self, task: DownloadTask) -> Path:
        """Atomically remove the incomplete prefix only after every image is ready."""
        completed_path, incomplete_path = self._task_path_pair(task)
        download_root = Path(task.download_root or Path(task.save_path).parent)
        if self._task_path_key(Path(task.save_path)) != self._task_path_key(incomplete_path):
            return validated_task_directory(Path(task.save_path), download_root)
        self._move_task_directory(task, completed_path, move_archive=False)
        return validated_task_directory(Path(task.save_path), download_root)

    def _normalize_persisted_task_paths(self, tasks: list[DownloadTask]) -> None:
        """Recover old or interrupted folder naming to match each persisted task status."""
        for task in tasks:
            if not task.save_path:
                continue
            completed_path, incomplete_path = self._task_path_pair(task)
            target_path = completed_path if task.status is TaskStatus.COMPLETED else incomplete_path
            counterpart_path = incomplete_path if task.status is TaskStatus.COMPLETED else completed_path
            if self._task_path_key(Path(task.save_path)) == self._task_path_key(target_path):
                target_archive = archive_path(target_path)
                counterpart_candidates = [counterpart_path]
                legacy_counterpart = self._legacy_incomplete_task_directory(completed_path)
                if self._task_path_key(legacy_counterpart) != self._task_path_key(counterpart_path):
                    counterpart_candidates.append(legacy_counterpart)
                for recovery_source in counterpart_candidates:
                    counterpart_archive = archive_path(recovery_source)
                    counterpart_needs_recovery = not target_path.exists() and recovery_source.exists()
                    archive_needs_recovery = (
                        task.status is TaskStatus.COMPLETED
                        and not target_archive.exists()
                        and counterpart_archive.exists()
                    )
                    if not counterpart_needs_recovery and not archive_needs_recovery:
                        continue
                    try:
                        self._move_task_directory(
                            task,
                            target_path,
                            move_archive=task.status is TaskStatus.COMPLETED,
                            source_override=recovery_source,
                        )
                    except Exception as error:
                        logger.warning("Interrupted task path recovery failed", task_id=task.id, error=str(error))
                    break
                continue
            source_override = None
            current_path = Path(task.save_path)
            if not current_path.exists() and counterpart_path.exists():
                source_override = counterpart_path
            try:
                self._move_task_directory(
                    task,
                    target_path,
                    move_archive=task.status is TaskStatus.COMPLETED,
                    source_override=source_override,
                )
            except Exception as error:
                logger.warning("Task directory state recovery failed", task_id=task.id, error=str(error))

    def _initialize_task_paths(self, tasks: list[DownloadTask] | None = None) -> None:
        """Reserve persisted paths once so title collisions never merge task output."""
        with self._lock:
            if self._task_paths_initialized:
                return
            known_tasks = tasks if tasks is not None else self._repository.get_all_tasks()
            for task in known_tasks:
                if not task.save_path:
                    continue
                completed_path, incomplete_path = self._task_path_pair(task)
                self._reserved_task_paths.add(self._task_path_key(completed_path))
                self._reserved_task_paths.add(self._task_path_key(incomplete_path))
            self._task_paths_initialized = True

    def _migrate_unused_legacy_task_paths(self, tasks: list[DownloadTask]) -> None:
        """Move only unmaterialized legacy ``title [aid]`` records to title-only paths."""
        migration_candidates: list[tuple[DownloadTask, Path, Path]] = []
        occupied_paths: set[str] = set()
        for task in tasks:
            if not task.save_path:
                continue
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
                completed_path, incomplete_path = self._task_path_pair(task)
                occupied_paths.add(self._task_path_key(completed_path))
                occupied_paths.add(self._task_path_key(incomplete_path))

        for task, old_path, download_root in migration_candidates:
            base_path = task_directory(download_root, task.comic.title)
            completed_candidate = base_path
            suffix = 2
            while (
                self._task_path_key(completed_candidate) in occupied_paths
                or self._task_path_key(incomplete_task_directory(completed_candidate)) in occupied_paths
                or self._path_or_archive_exists(completed_candidate)
                or self._path_or_archive_exists(incomplete_task_directory(completed_candidate))
            ):
                completed_candidate = base_path.with_name(f"{base_path.name} ({suffix})")
                suffix += 1
            candidate = (
                completed_candidate
                if task.status is TaskStatus.COMPLETED
                else incomplete_task_directory(completed_candidate)
            )
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
            occupied_paths.add(self._task_path_key(completed_candidate))
            occupied_paths.add(self._task_path_key(incomplete_task_directory(completed_candidate)))
            logger.info("Migrated unused legacy task path", task_id=task.id)

    def _reserve_task_directory(self, download_root: Path, title: str) -> Path:
        """Reserve a title-only path, adding a numeric suffix for genuine collisions."""
        self._initialize_task_paths()
        base_path = task_directory(download_root, title)
        completed_candidate = base_path
        suffix = 2
        with self._lock:
            while (
                self._task_path_key(completed_candidate) in self._reserved_task_paths
                or self._task_path_key(incomplete_task_directory(completed_candidate)) in self._reserved_task_paths
                or self._path_or_archive_exists(completed_candidate)
                or self._path_or_archive_exists(incomplete_task_directory(completed_candidate))
            ):
                completed_candidate = base_path.with_name(f"{base_path.name} ({suffix})")
                suffix += 1
            incomplete_candidate = incomplete_task_directory(completed_candidate)
            self._reserved_task_paths.add(self._task_path_key(completed_candidate))
            self._reserved_task_paths.add(self._task_path_key(incomplete_candidate))
        return incomplete_candidate

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
                try:
                    self._ensure_incomplete_task_directory(existing)
                except Exception as error:
                    logger.error("Failed to mark task directory incomplete", task_id=existing.id, error=str(error))
                    self.signals.task_error.emit(existing.id, str(error))
                    return existing
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
                completed_path = completed_task_directory(save_path)
                self._reserved_task_paths.discard(self._task_path_key(save_path))
                self._reserved_task_paths.discard(self._task_path_key(completed_path))
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
        task_path = Path(task.save_path)
        if not delete_files:
            forget_manifest(task_path, task.id, ARTIFACT_METADATA_DIR)
            return
        recorded_root = Path(task.download_root or task_path.parent)
        safe_task_path = validated_task_directory(task_path, recorded_root)
        remove_owned_artifacts(
            task_id=task.id,
            source_directory=safe_task_path,
            expected_files=expected_files or [],
            metadata_directory=ARTIFACT_METADATA_DIR,
        )
        if safe_task_path.exists():
            logger.info("Deleted task-owned files", path=str(safe_task_path), task_id=task.id)

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
        existing_path: Path | None = None
        for path in expected_paths:
            if await asyncio.to_thread(is_valid_image, path, cfg.max_image_pixels):
                existing_path = path
                break
        if existing_path is not None:
            if image["status"] != "downloaded" or image["output_name"] != existing_path.name:
                await asyncio.to_thread(
                    self._repository.update_image_status,
                    task.id,
                    index,
                    "downloaded",
                    existing_path.name,
                )
            if image["status"] != "downloaded":
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
                output_lock = self._output_locks.setdefault(task.id, asyncio.Lock())
                async with output_lock:
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
                await asyncio.to_thread(
                    self._repository.update_image_status,
                    task.id,
                    index,
                    "downloaded",
                    published_output.name,
                )
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
        task: DownloadTask | None = None
        directory_finalized = False
        try:
            task = await asyncio.to_thread(self._repository.get_task, task_id)
            if task is None:
                return
            if task.options is None:
                task.options = self._options_from_config(naming_version=1)
                task.download_root = task.download_root or str(Path(task.save_path).parent)
                await asyncio.to_thread(self._repository.save_task, task)
            options = task.options
            if options.naming.value == "original" and options.naming_version == 2:
                options = options.model_copy(update={"naming_version": 1})
                task.options = options
                await asyncio.to_thread(self._repository.save_task, task)
            if not await asyncio.to_thread(self._repository.claim_pending_task, task_id):
                return
            task.status = TaskStatus.DOWNLOADING
            connection_limiter = self._connection_limiter
            if connection_limiter is None:
                raise RuntimeError("Connection limiter is not initialized")

            with self._lock:
                if task_id not in self._active_tasks:
                    return
                self._cancel_events[task_id] = cancel_event

            await asyncio.to_thread(self._ensure_incomplete_task_directory, task)
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
                valid_path: Path | None = None
                for path in expected_image_paths(task, image, options):
                    if await asyncio.to_thread(is_valid_image, path, cfg.max_image_pixels):
                        valid_path = path
                        break
                if valid_path is not None and options.naming.value == "original" and options.naming_version == 1:
                    preferred = preferred_image_paths(task, image, options)
                    legacy_options = options.model_copy(update={"naming_version": 2})
                    legacy_paths = preferred_image_paths(task, image, legacy_options)
                    if valid_path in legacy_paths:
                        migration_target = next(
                            (
                                candidate
                                for candidate in preferred
                                if candidate.suffix.casefold() == valid_path.suffix.casefold()
                            ),
                            None,
                        )
                        if migration_target is not None:
                            base_target = migration_target
                            suffix = 2
                            while migration_target.exists():
                                migration_target = base_target.with_name(
                                    f"{base_target.stem} ({suffix}){base_target.suffix}"
                                )
                                suffix += 1
                            await asyncio.to_thread(valid_path.replace, migration_target)
                            valid_path = migration_target
                valid = valid_path is not None
                if valid_path is not None:
                    existing_bytes += valid_path.stat().st_size
                target_status = "downloaded" if valid else "pending"
                target_output_name = valid_path.name if valid_path is not None else ""
                if image["status"] != target_status or image["output_name"] != target_output_name:
                    await asyncio.to_thread(
                        self._repository.update_image_status,
                        task_id,
                        image["image_index"],
                        target_status,
                        target_output_name,
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
            await asyncio.to_thread(self._finalize_task_directory, task)
            directory_finalized = True
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
                metadata_directory=ARTIFACT_METADATA_DIR,
            )
            await asyncio.to_thread(self._repository.update_task_status, task_id, TaskStatus.COMPLETED)
            self.signals.task_status_changed.emit(task_id, TaskStatus.COMPLETED)
        except Exception as error:
            if not cancel_event.is_set():
                if directory_finalized and task is not None:
                    try:
                        await asyncio.to_thread(self._ensure_incomplete_task_directory, task)
                    except Exception as path_error:
                        logger.error(
                            "Failed to restore incomplete task directory prefix",
                            task_id=task_id,
                            error=str(path_error),
                        )
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
                self._output_locks.pop(task_id, None)
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
