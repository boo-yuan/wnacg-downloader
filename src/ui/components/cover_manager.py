import os
import shutil
import hashlib
import atexit
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool
from PySide6.QtGui import QImage
from curl_cffi import requests
from core.config import cfg
from core.logger import logger

TEMP_DIR = "data/.temp_covers"

def _cleanup_covers():
    try:
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    except Exception:
        pass

# Register cleanup on exit
atexit.register(_cleanup_covers)

class CoverManagerSignals(QObject):
    finished = Signal(str, QImage)

class CoverFetchTask(QRunnable):
    def __init__(self, url, signals):
        super().__init__()
        self.url = url
        self.signals = signals
        
    def run(self):
        filename = hashlib.md5(self.url.encode()).hexdigest() + ".jpg"
        filepath = os.path.join(TEMP_DIR, filename)
        
        # 1. Load from disk if exists
        if os.path.exists(filepath):
            img = QImage(filepath)
            if not img.isNull():
                self.signals.finished.emit(self.url, img)
                return
            else:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            
            
        # 2. Download and write to disk
        kwargs = {
            "impersonate": "chrome",
            "verify": False,
            "timeout": 15.0
        }
        if cfg.proxy_mode == "custom":
            kwargs["proxies"] = cfg.curl_cffi_proxies
        elif cfg.proxy_mode == "direct":
            kwargs["trust_env"] = False
        else:
            kwargs["trust_env"] = True
            
        for attempt in range(3):
            try:
                with requests.Session(**kwargs) as s:
                    resp = s.get(self.url)
                    
                if resp.status_code == 200:
                    img = QImage()
                    if img.loadFromData(resp.content):
                        with open(filepath, 'wb') as f:
                            f.write(resp.content)
                        self.signals.finished.emit(self.url, img)
                        return
                    else:
                        raise Exception("Invalid image data")
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"CoverFetchTask failed for {self.url} after 3 attempts: {e}")
                else:
                    import time
                    time.sleep(1.0)
                    
        self.signals.finished.emit(self.url, QImage())

class CoverManagerClass(QObject):
    def __init__(self):
        super().__init__()
        if not os.path.exists(TEMP_DIR):
            os.makedirs(TEMP_DIR, exist_ok=True)
            
        self.pool = QThreadPool.globalInstance()
        # Limit to 5 concurrent cover downloads to prevent anti-bot blocking
        # QThreadPool.setMaxThreadCount applies to all tasks in the global instance
        # If maxThreadCount is lower than 5, we bump it to handle IO easily.
        # But wait, QThreadPool usually has lots of threads (e.g. 8-16 depending on CPU)
        # To limit only covers to 5, we should probably just use our own thread pool!
        self.cover_pool = QThreadPool()
        self.cover_pool.setMaxThreadCount(5)
        self.signals = CoverManagerSignals()
        self.signals.finished.connect(self._on_task_finished)
        
        # In-flight task tracker to avoid duplicate downloads of the same URL
        self._pending_callbacks = {}
        
    def load(self, url, callback=None):
        if not url: return
        
        filename = hashlib.md5(url.encode()).hexdigest() + ".jpg"
        filepath = os.path.join(TEMP_DIR, filename)
        
        if os.path.exists(filepath):
            if callback:
                img = QImage(filepath)
                callback(url, img)
            return
            
        if url in self._pending_callbacks:
            if callback:
                self._pending_callbacks[url].append(callback)
            return
            
        self._pending_callbacks[url] = [callback] if callback else []
        task = CoverFetchTask(url, self.signals)
        self.cover_pool.start(task)
        
    def preload(self, url):
        self.load(url, None)
        
    def _on_task_finished(self, url, img):
        if url in self._pending_callbacks:
            cbs = self._pending_callbacks.pop(url)
            for cb in cbs:
                if cb:
                    try:
                        cb(url, img)
                    except Exception as e:
                        logger.error(f"Callback error in CoverManager: {e}")

    def stop(self):
        self.cover_pool.clear()
        self.cover_pool.waitForDone(1000)

# Global singleton
cover_manager = CoverManagerClass()
