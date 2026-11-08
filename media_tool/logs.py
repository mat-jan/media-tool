"""Logowanie do pliku z rotacją + dziennik operacji (audyt)."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List

from . import config

LOGGER_NAME = "media_tool"
_configured = False


def log_file() -> Path:
    return config.log_dir() / "media-tool.log"


def setup(level: str = "INFO", verbose: bool = False) -> logging.Logger:
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    try:
        log_file().parent.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(h)
    except OSError:
        logger.addHandler(logging.NullHandler())  # brak możliwości zapisu logów nie psuje programu
    if verbose:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(sh)
        logger.setLevel(logging.DEBUG)
    _configured = True
    return logger


def reset_for_tests() -> None:
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)
    _configured = False


def get() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def audit(operation: str, **fields) -> None:
    """Zapisuje jedną linię JSON opisującą operację (start/koniec/odmowa)."""
    get().info("AUDIT %s", json.dumps({"op": operation, **fields}, ensure_ascii=False, default=str))


def tail(lines: int = 50) -> List[str]:
    try:
        text = log_file().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]
