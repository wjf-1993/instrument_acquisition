# logger.py — 统一日志管理模块
from __future__ import annotations
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_loggers: dict = {}

def setup_logging(log_dir="logs", log_file="acquisition.log", level="INFO",
                   max_bytes=10485760, backup_count=5, console_output=True):
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.handlers.clear()
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    file_path = log_path / log_file
    file_handler = RotatingFileHandler(filename=str(file_path), maxBytes=max_bytes,
                                        backupCount=backup_count, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    return root_logger

def setup_logging_from_config(config):
    return setup_logging(log_dir=config.logging.log_dir, log_file=config.logging.log_file,
                         level=config.logging.level, max_bytes=config.logging.max_bytes,
                         backup_count=config.logging.backup_count,
                         console_output=config.logging.console_output)

def get_logger(name):
    if name in _loggers:
        return _loggers[name]
    logger = logging.getLogger(name)
    _loggers[name] = logger
    return logger