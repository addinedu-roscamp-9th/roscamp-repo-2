from __future__ import annotations

import logging
import os
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


def _should_suppress_terminal(src: str, level: str, event: str, detail: str) -> bool:
    """
    터미널 출력만 조용히 만들기 위한 필터.
    - DB(event_log)에는 계속 기록합니다.
    - 현재: 관리자 GUI가 arm 쪽에 CAM_START/CAM_STOP 등을 계속 넣고,
      arm 워커가 unsupported로 IGNORED 처리하면서 로그가 도배되는 상황을 막습니다.

    필요하면 환경변수로 끌 수 있습니다:
      TASHO_SUPPRESS_NOISE=0  -> 필터 끔
    """
    # 기본값: ON
    if os.getenv("TASHO_SUPPRESS_NOISE", "1").strip() in ("0", "false", "False", "OFF", "off"):
        return False

    s = (src or "").upper()
    e = (event or "").upper()
    d = (detail or "")
    d_u = d.upper()

    # 1) CAM_* 관련 arm queue/claim/ack 로그는 터미널에서 숨김
    cam_related = ("CMD=CAM_" in d_u) or ("UNSUPPORTED CMD: CAM_" in d_u)

    if cam_related and e in ("ARM_CMD_QUEUED", "ARM_CMD_CLAIMED", "ARM_CMD_ACK", "ARM_CMD_IGNORED"):
        # src가 GUI/SERVER/ARM 어디든 CAM 관련이면 조용히
        return True

    return False


def log_event(
    src: str,
    level: str,
    event: str,
    detail: str,
    robot_id: Optional[str] = None,
) -> None:
    """
    Non-critical logging:
      - Print to terminal (unless suppressed)
      - Try DB insert; if fail, print warning (but never raise)
    """
    logger = get_logger()

    lvl = (level or "INFO").upper()

    # ✅ 터미널 출력 억제(필요한 것만)
    if not _should_suppress_terminal(src, lvl, event, detail):
        if lvl == "ERROR":
            logger.error("%s | %s | %s", src, event, detail)
        elif lvl in ("WARN", "WARNING"):
            logger.warning("%s | %s | %s", src, event, detail)
        else:
            logger.info("%s | %s | %s", src, event, detail)

    # ✅ DB에는 그대로 남김(원하면 이것도 suppress 조건으로 막을 수 있음)
    try:
        db_execute(
            """
            INSERT INTO event_log (src, level, event, detail, robot_id, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW(6))
            """,
            (src, lvl, event, detail, robot_id),
        )
    except Exception as e:
        # DB 실패 로그는 CAM 필터와 무관하게 보여주는 편이 안전
        logger.warning("DB log_event failed: %s", repr(e))
