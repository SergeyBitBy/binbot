import gzip
import logging
import os
import shutil
import sys
from logging.handlers import TimedRotatingFileHandler

from app.config.settings import LOGS_DIR, settings


def _gzip_namer(name: str) -> str:
    return f"{name}.gz"


def _gzip_rotator(source: str, destination: str) -> None:
    with open(source, "rb") as source_file, gzip.open(destination, "wb") as target_file:
        shutil.copyfileobj(source_file, target_file)
    os.remove(source)


def setup_logging():
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    file_handler = TimedRotatingFileHandler(
        LOGS_DIR / "bot.log", when="midnight", interval=1,
        backupCount=settings.log_retention_days, encoding="utf-8", utc=True,
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
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
