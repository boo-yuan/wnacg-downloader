"""Structured, append-only application logging configuration."""

import sys
import threading
import traceback
from types import TracebackType

from loguru import logger

from wnacg.infrastructure.paths import DATA_DIR, path_migration_warnings

LOG_PATH = DATA_DIR / "app.jsonl"
_configured = False


def handle_exception(
    exception_type: type[BaseException],
    exception: BaseException,
    exception_traceback: TracebackType | None,
) -> None:
    """Log uncaught main-thread exceptions without hiding keyboard interrupts."""
    if issubclass(exception_type, KeyboardInterrupt):
        sys.__excepthook__(exception_type, exception, exception_traceback)
        return
    logger.opt(exception=(exception_type, exception, exception_traceback)).critical("Uncaught main-thread exception")


def handle_thread_exception(arguments: threading.ExceptHookArgs) -> None:
    """Log uncaught worker-thread exceptions."""
    formatted = "".join(traceback.format_exception(arguments.exc_type, arguments.exc_value, arguments.exc_traceback))
    thread_name = arguments.thread.name if arguments.thread is not None else "unknown"
    logger.critical("Uncaught thread exception", thread=thread_name, traceback=formatted)


def configure_logging() -> None:
    """Install logging sinks and exception hooks once during explicit startup."""
    global _configured
    if _configured:
        return
    logger.remove()
    if sys.stderr is not None:
        logger.add(sys.stderr, level="INFO", colorize=True)
    logger.add(
        LOG_PATH,
        level="INFO",
        rotation="5 MB",
        retention="7 days",
        serialize=True,
        enqueue=True,
        encoding="utf-8",
        mode="a",
    )
    for migration_warning in path_migration_warnings():
        logger.warning("Data migration warning", detail=migration_warning)
    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception
    _configured = True


def complete_logging() -> None:
    """Flush queued sinks before process shutdown."""
    logger.complete()


__all__ = ["LOG_PATH", "complete_logging", "configure_logging", "logger"]
