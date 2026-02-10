from __future__ import annotations

import logging
import sys
from typing import Optional

from .db import db_execute

_LOGGER: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    logger = logging.getLogger("tasho_server")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        h.setFormatter(fmt)
        logger.addHandler(h)

    logger.propagate = False
    _LOGGER = logger
    return logger


def log_event(src: str, level: str, event: str, detail: str) -> None:
    """
    Non-critical logging:
      - Always print to terminal
      - Try DB insert; if fail, print warning (but never raise)
    """
    logger = get_logger()

    lvl = (level or "INFO").upper()
    if lvl == "ERROR":
        logger.error("%s | %s | %s", src, event, detail)
    elif lvl == "WARN" or lvl == "WARNING":
        logger.warning("%s | %s | %s", src, event, detail)
    else:
        logger.info("%s | %s | %s", src, event, detail)

    try:
        db_execute(
            "INSERT INTO event_log (src, level, event, detail, created_at) VALUES (%s,%s,%s,%s,NOW(6))",
            (src, lvl, event, detail),
        )
    except Exception as e:
        logger.warning("DB log_event failed: %s", repr(e))
