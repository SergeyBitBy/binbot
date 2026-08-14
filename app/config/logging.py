import gzip
import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config.settings import LOGS_DIR, settings


def _gzip_namer(name: str) -> str:
    return f"{name}.gz"


def _gzip_rotator(source: str, destination: str) -> None:
    with open(source, "rb") as source_file, gzip.open(destination, "wb") as target_file:
        shutil.copyfileobj(source_file, target_file)
    os.remove(source)


def _prune_logs(log_dir: Path, max_total_bytes: int) -> None:
    files = [path for path in log_dir.glob("bot.log*") if path.is_file()]
    total_size = sum(path.stat().st_size for path in files)
    archived = sorted(
        (path for path in files if path.name != "bot.log"),
        key=lambda path: path.stat().st_mtime,
    )
    for path in archived:
        if total_size <= max_total_bytes:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        total_size -= size


def setup_logging():
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    max_file_bytes = max(1, settings.log_max_file_mb) * 1024 * 1024
    max_total_bytes = max(settings.log_max_file_mb, settings.log_max_total_mb) * 1024 * 1024
    backup_count = max(1, max_total_bytes // max_file_bytes - 1)
    _prune_logs(LOGS_DIR, max_total_bytes)

    file_handler = RotatingFileHandler(
        LOGS_DIR / "bot.log",
        maxBytes=max_file_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.namer = _gzip_namer
    file_handler.rotator = _gzip_rotator
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    file_handler.setLevel(log_level)
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    console_handler.setLevel(log_level)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Avoid duplicate handlers on re-initialization
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Suppress verbose third-party loggers if needed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
