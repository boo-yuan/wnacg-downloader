from loguru import logger
import sys

# 清除默认的所有 handler
logger.remove()

# 添加终端高亮输出
logger.add(sys.stdout, level="INFO", colorize=True)

# 添加文件输出，每次启动时清空覆盖 (mode="w")
# 只保留 INFO 及以上的重点信息
logger.add(
    "app.log", 
    level="INFO", 
    mode="w", 
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}",
    enqueue=True, # 确保在多线程(如下载引擎)下写入安全
    encoding="utf-8"
)

__all__ = ["logger"]
