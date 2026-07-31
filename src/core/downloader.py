import asyncio
import os
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QObject
from core.models import DownloadTask, Comic, TaskStatus
from core.crawler import WnacgCrawler
from core.config import cfg
from core.logger import logger
import core.db as db
import uuid
import PIL.Image
import random

def is_valid_image(file_path: Path) -> bool:
    if not file_path.exists() or file_path.stat().st_size == 0:
        return False
    try:
        with PIL.Image.open(file_path) as img:
            img.verify()
        # verify() only checks header. We must load() to catch truncated (half-downloaded) images.
        # PIL.ImageFile.LOAD_TRUNCATED_IMAGES is False by default, so load() will raise OSError if truncated.
        with PIL.Image.open(file_path) as img:
            img.load()
        return True
    except Exception:
        return False

class DownloaderSignals(QObject):
    task_added = Signal(DownloadTask)
    task_progress = Signal(str, int, int) # task_id, downloaded, total
    task_status_changed = Signal(str, object) # task_id, TaskStatus enum
    task_error = Signal(str, str) # task_id, error_message
    badge_update = Signal(int) # active tasks count

class DownloaderWorker(QThread):
    def __init__(self):
        super().__init__()
        self.signals = DownloaderSignals()
        self._loop = None
        self._active_tasks = {} # task_id -> concurrent.futures.Future
        self._cancel_events = {} # task_id -> asyncio.Event
        self._global_semaphore = None
        
        # Reset any leftover DOWNLOADING tasks to PAUSED on startup
        db.reset_downloading_tasks()
        
        # Check for deleted folders for completed tasks
        tasks = db.get_all_tasks()
        for task in tasks:
            if task.status == TaskStatus.COMPLETED:
                save_path = Path(task.save_path)
                if not save_path.exists() and not save_path.with_suffix('.zip').exists():
                    db.delete_task(task.id)

    def add_task(self, comic: Comic) -> DownloadTask:
        existing = db.get_task_by_aid(comic.aid)
        if existing:
            if existing.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING):
                return existing
            elif existing.status in (TaskStatus.PAUSED, TaskStatus.FAILED):
                if cfg.auto_start_download and self._loop:
                    self.resume_task(existing.id)
                return existing
            elif existing.status == TaskStatus.COMPLETED:
                existing.status = TaskStatus.PENDING if cfg.auto_start_download else TaskStatus.PAUSED
                existing.downloaded_images = 0
                existing.progress = 0
                existing.error_message = ""
                db.save_task(existing)
                self.signals.task_status_changed.emit(existing.id, existing.status)
                self.signals.task_progress.emit(existing.id, 0, existing.total_images)
                if cfg.auto_start_download and self._loop:
                    self.resume_task(existing.id)
                return existing

        task_id = str(uuid.uuid4())
        save_path = Path(cfg.download_dir) / self._clean_filename(comic.title)
        
        initial_status = TaskStatus.PENDING if cfg.auto_start_download else TaskStatus.PAUSED
        task = DownloadTask(
            id=task_id,
            comic=comic,
            save_path=str(save_path),
            status=initial_status
        )
        db.save_task(task)
        self.signals.task_added.emit(task)
        self._update_badge()
        
        if cfg.auto_start_download and self._loop:
            self.resume_task(task.id)
            
        return task
        
    def _clean_filename(self, name: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        for c in invalid_chars:
            name = name.replace(c, '')
        return name.strip().rstrip('.')

    def pause_tasks(self, task_ids: list[str]):
        for task_id in task_ids:
            if task_id in self._cancel_events:
                self._cancel_events[task_id].set()
            if task_id in self._active_tasks:
                coro = self._active_tasks.pop(task_id)
                coro.cancel()
            db.update_task_status(task_id, TaskStatus.PAUSED)
            self.signals.task_status_changed.emit(task_id, TaskStatus.PAUSED)
        self._update_badge()
        self._check_queue()

    def pause_task(self, task_id: str):
        self.pause_tasks([task_id])

    def resume_tasks(self, task_ids: list[str]):
        for task_id in task_ids:
            if self._loop and task_id not in self._active_tasks:
                db.update_task_status(task_id, TaskStatus.PENDING)
                self.signals.task_status_changed.emit(task_id, TaskStatus.PENDING)
        self._update_badge()
        self._check_queue()

    def resume_task(self, task_id: str):
        self.resume_tasks([task_id])

    def _check_queue(self):
        if not self._loop: return
        if len(self._active_tasks) >= cfg.max_concurrent_tasks: return
        
        tasks = db.get_all_tasks()
        pending_tasks = [t for t in tasks if t.status == TaskStatus.PENDING and t.id not in self._active_tasks]
        
        if pending_tasks:
            logger.info(f"Checking queue: {len(self._active_tasks)} active, {len(pending_tasks)} pending, max {cfg.max_concurrent_tasks}")
            
        for t in pending_tasks:
            if len(self._active_tasks) >= cfg.max_concurrent_tasks:
                break
            task_id = t.id
            coro = asyncio.run_coroutine_threadsafe(self._process_task(task_id), self._loop)
            self._active_tasks[task_id] = coro
            logger.info(f"Started task {task_id}, active tasks: {len(self._active_tasks)}")
            
        self._update_badge()

    def cancel_tasks(self, task_ids: list[str]):
        for task_id in task_ids:
            task = db.get_task(task_id)
            if task:
                if task.status != TaskStatus.COMPLETED:
                    if task_id in self._cancel_events:
                        self._cancel_events[task_id].set()
                    if task_id in self._active_tasks:
                        coro = self._active_tasks.pop(task_id)
                        coro.cancel()
                    # Do not delete the folder to allow keeping already downloaded images
            db.delete_task(task_id)
            self.signals.task_status_changed.emit(task_id, TaskStatus.CANCELED)
        self._update_badge()
        self._check_queue()

    def cancel_task(self, task_id: str):
        self.cancel_tasks([task_id])

    def _update_badge(self):
        tasks = db.get_all_tasks()
        active_count = sum(1 for t in tasks if t.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING))
        self.signals.badge_update.emit(active_count)

    async def _process_task(self, task_id: str):
        try:
            task = db.get_task(task_id)
            if not task: return
            
            task_semaphore = asyncio.Semaphore(max(1, cfg.global_max_connections // max(1, cfg.max_concurrent_tasks)))
            
            cancel_event = asyncio.Event()
            self._cancel_events[task_id] = cancel_event
            
            db.update_task_status(task_id, TaskStatus.DOWNLOADING)
            self.signals.task_status_changed.emit(task_id, TaskStatus.DOWNLOADING)
            
            Path(task.save_path).mkdir(parents=True, exist_ok=True)
            
            images = db.get_images(task_id)
            if not images:
                view_links = await WnacgCrawler.get_image_view_links(task.comic.aid)
                db.save_view_links(task_id, view_links)
                images = db.get_images(task_id)
                
            task.total_images = len(images)
            if task.total_images == 0:
                raise Exception("No images found in index page")
            
            # Recalculate downloaded_images from local verification for ALL images
            downloaded_count = 0
            for img in images:
                naming = cfg.download_naming
                target_format = cfg.download_format
                raw_url = img['raw_url']
                idx = img['image_index']
                
                if naming == "original" and raw_url:
                    base_name = Path(raw_url).stem
                else:
                    base_name = f"{idx+1:03d}"
                    
                if target_format == "original":
                    expected_files = [f"{base_name}{ext}" for ext in ('.jpg', '.png', '.jpeg', '.gif', '.webp')]
                else:
                    expected_files = [f"{base_name}.{target_format}"]
                    
                valid_found = False
                for ef in expected_files:
                    fp = Path(task.save_path) / ef
                    if await asyncio.to_thread(is_valid_image, fp):
                        valid_found = True
                        break
                        
                if valid_found:
                    downloaded_count += 1
                    if img['status'] != 'downloaded':
                        db.update_image_status(task_id, idx, 'downloaded')
                else:
                    if img['status'] == 'downloaded':
                        db.update_image_status(task_id, idx, 'pending')
                        
            task.downloaded_images = downloaded_count
            db.update_task_progress(task_id, 0, task.downloaded_images, task.total_images)
            self.signals.task_progress.emit(task_id, task.downloaded_images, task.total_images)
            
            if task.downloaded_images >= task.total_images:
                db.update_task_status(task_id, TaskStatus.COMPLETED)
                self.signals.task_status_changed.emit(task_id, TaskStatus.COMPLETED)
                return


            
            def process_and_save_image(image_data: bytes, save_dir: Path, idx: int, raw_url: str) -> bool:
                try:
                    import io
                    with PIL.Image.open(io.BytesIO(image_data)) as img:
                        target_format = cfg.download_format
                        img_format = img.format.lower() if img.format else "jpg"
                        if target_format == "original":
                            target_format = img_format
                            if target_format == 'jpeg': target_format = 'jpg'
                            
                        suffix = f".{target_format}"
                        
                        naming = cfg.download_naming
                        if naming == "original" and raw_url:
                            base_name = Path(raw_url).stem
                        else:
                            base_name = f"{idx+1:03d}"
                            
                        final_path = save_dir / f"{base_name}{suffix}"
                        
                        out_img = img
                        if target_format == "jpg" and out_img.mode in ("RGBA", "P"):
                            background = PIL.Image.new("RGB", out_img.size, (255, 255, 255))
                            if out_img.mode == "RGBA":
                                background.paste(out_img, mask=out_img.split()[3])
                            else:
                                background.paste(out_img, mask=out_img.convert("RGBA").split()[3])
                            out_img = background
                        elif out_img.mode != "RGB" and target_format == "jpg":
                            out_img = out_img.convert("RGB")
                            
                        save_kwargs = {}
                        if target_format == "jpg": save_kwargs["quality"] = 95
                        
                        out_img.save(final_path, format="JPEG" if target_format == "jpg" else target_format.upper(), **save_kwargs)
                        
                    if is_valid_image(final_path):
                        return True
                    else:
                        final_path.unlink(missing_ok=True)
                        return False
                except Exception as e:
                    logger.error(f"Image processing failed for {raw_url}: {e}")
                    return False
            
            async def download_image(img_dict):
                idx = img_dict["image_index"]
                view_url = img_dict["view_url"]
                raw_url = img_dict["raw_url"]
                status = img_dict["status"]
                
                if cancel_event.is_set(): return False
                
                async with task_semaphore:
                    if cfg.download_delay > 0:
                        jitter = random.uniform(0.7, 1.3)
                        await asyncio.sleep(cfg.download_delay * jitter)
                        
                    # Fetch raw url if missing
                    if not raw_url:
                        for attempt in range(3):
                            if cancel_event.is_set(): return False
                            raw_url = await WnacgCrawler.get_raw_image_url(view_url, client)
                            if raw_url:
                                db.update_image_raw_url(task_id, idx, raw_url)
                                break
                            else:
                                if attempt == 2:
                                    logger.error(f"Failed to get raw url for {view_url} after 3 attempts")
                                    return False
                                await asyncio.sleep(2.0)
                            
                # Check if already downloaded
                naming = cfg.download_naming
                target_format = cfg.download_format
                if naming == "original" and raw_url:
                    base_name = Path(raw_url).stem
                else:
                    base_name = f"{idx+1:03d}"
                    
                if target_format == "original":
                    expected_files = [f"{base_name}{ext}" for ext in ('.jpg', '.png', '.jpeg', '.gif', '.webp')]
                else:
                    expected_files = [f"{base_name}.{target_format}"]
                    
                for ef in expected_files:
                    fp = Path(task.save_path) / ef
                    if await asyncio.to_thread(is_valid_image, fp):
                        if status != 'downloaded':
                            db.update_image_status(task_id, idx, 'downloaded')
                            task.downloaded_images += 1
                            db.update_task_progress(task_id, 0, task.downloaded_images, task.total_images)
                            self.signals.task_progress.emit(task_id, task.downloaded_images, task.total_images)
                        return True
                
                if cancel_event.is_set(): return False
                
                # No temp file needed, process directly from memory
                async with task_semaphore:
                    if cfg.download_delay > 0:
                        jitter = random.uniform(0.7, 1.3)
                        await asyncio.sleep(cfg.download_delay * jitter)
                        
                    for attempt in range(3):
                        if cancel_event.is_set(): return False
                        try:
                            resp = await client.get(raw_url, timeout=30.0)
                            if resp.status_code == 200:
                                success = await asyncio.to_thread(
                                    process_and_save_image, resp.content, Path(task.save_path), idx, raw_url
                                )
                                if success:
                                    db.update_image_status(task_id, idx, 'downloaded')
                                    task.downloaded_images += 1
                                    db.update_task_progress(task_id, 0, task.downloaded_images, task.total_images)
                                    self.signals.task_progress.emit(task_id, task.downloaded_images, task.total_images)
                                    return True
                        except Exception as e:
                            if attempt == 2:
                                logger.error(f"Failed to download image {idx} after 3 attempts: {e}")
                            else:
                                await asyncio.sleep(2.0)
                                
                    return False

            images = db.get_images(task_id) # reload status
            
            async with WnacgCrawler.get_client() as client:
                try:
                    async with asyncio.TaskGroup() as tg:
                        for img in images:
                            if img['status'] != 'downloaded':
                                tg.create_task(download_image(img))
                except ExceptionGroup as eg:
                    for exc in eg.exceptions:
                        logger.error(f"Image download exception: {exc}")
                    raise Exception("Some images failed to download") from eg
            
            if cancel_event.is_set():
                # Status already handled by pause_task or cancel_task
                pass
            elif task.downloaded_images >= task.total_images:
                if cfg.pack_to_zip:
                    def create_zip(source_dir, delete_original):
                        import zipfile, shutil
                        if not source_dir.exists(): return
                        zip_path = source_dir.with_suffix('.zip')
                        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                            for root, dirs, files in os.walk(source_dir):
                                for file in files:
                                    file_path = Path(root) / file
                                    zipf.write(file_path, file_path.relative_to(source_dir))
                        if delete_original:
                            shutil.rmtree(source_dir, ignore_errors=True)
                            
                    await asyncio.to_thread(create_zip, Path(task.save_path), cfg.delete_original_after_pack)
                    
                db.update_task_status(task_id, TaskStatus.COMPLETED)
                self.signals.task_status_changed.emit(task_id, TaskStatus.COMPLETED)
            else:
                missing = task.total_images - task.downloaded_images
                raise Exception(f"缺失 {missing} 张图片未能成功下载")
                
        except Exception as e:
            if 'cancel_event' not in locals() or not cancel_event.is_set():
                logger.error(f"Task {task_id} failed: {e}")
                db.update_task_status(task_id, TaskStatus.FAILED, str(e))
                self.signals.task_error.emit(task_id, str(e))
                self.signals.task_status_changed.emit(task_id, TaskStatus.FAILED)
        finally:
            if task_id in self._cancel_events:
                del self._cancel_events[task_id]
            if task_id in self._active_tasks:
                del self._active_tasks[task_id]
            self._update_badge()
            self._check_queue()

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        # We don't automatically resume PENDING here. UI will trigger resume or we can auto resume.
        # But for robustness, let's auto resume PENDING
        tasks = db.get_all_tasks()
        for task in tasks:
            if task.status == TaskStatus.PENDING:
                self.resume_task(task.id)
                
        self._loop.run_forever()

    def stop(self):
        # Cancel all active tasks to allow clean exit
        for cancel_event in self._cancel_events.values():
            cancel_event.set()
            
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            
        self.wait(2000)

# 全局单例调度器
downloader_manager = DownloaderWorker()
