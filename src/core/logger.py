import sys

from loguru import logger
from core.paths import DATA_DIR

# 清除默认的所有 handler
logger.remove()

# 添加终端高亮输出 (如果是 --windowed 模式，sys.stdout 可能为 None)
if sys.stdout is not None:
    logger.add(sys.stdout, level="INFO", colorize=True)

# 添加文件输出，每次启动时清空覆盖 (mode="w")
# 只保留 INFO 及以上的重点信息
logger.add(
    str(DATA_DIR / "app.log"), 
    level="INFO",  
    rotation="5 MB",
    retention="7 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}",
    enqueue=True, # 确保在多线程(如下载引擎)下写入安全
    encoding="utf-8"
)

__all__ = ["logger"]

import traceback

# 捕获全局未处理异常
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logger.error(f"Uncaught exception:\n{err_msg}")

sys.excepthook = handle_exception

# 捕获子线程中的未处理异常
import threading

def handle_thread_exception(args):
    err_msg = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    logger.error(f"Uncaught thread exception in {args.thread.name}:\n{err_msg}")

threading.excepthook = handle_thread_exception
