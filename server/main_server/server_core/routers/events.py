from __future__ import annotations

from typing import Optional
import json

from fastapi import APIRouter, Query
from pydantic import BaseModel

from server_core.db import db_fetchall
from server_core.log import log_event

router = APIRouter(tags=["events"])


EVENT_KO_MAP = {
    # Pinky
    "PINKY_CMD_ENQ": "핑키 명령 등록",
    "PINKY_CMD_ENQ_SUPPRESS": "핑키 명령 중복 억제",
    "PINKY_CMD_CLAIMED": "핑키 명령 수신",
    "PINKY_CMD_ACK": "핑키 명령 완료",
    # Arm
    "ARM_CMD_ENQ": "로봇팔 명령 등록",
    "ARM_CMD_ENQ_SUPPRESS": "로봇팔 명령 중복 억제",
    "ARM_CMD_QUEUED": "로봇팔 명령 큐 등록",
    "ARM_CMD_CLAIMED": "로봇팔 명령 수신",
    "ARM_CMD_ACK": "로봇팔 명령 완료",
    "ARM_DETECTED": "로봇팔 감지",
    "ARM_WARN_SET": "로봇팔 경고 발생",
    "ARM_WARN_CLEARED": "로봇팔 경고 해제",
    "ARM_CMD_DROPPED": "로봇팔 명령 무시",
    # Autos sequence
    "AUTOSEQ_AFTER_CHARGE_SKIPPED": "후속 자동명령 생략",
    # Payment/system
    "PAYMENT_APPROVED": "결제 승인 저장",
    "SCHEMA_OK": "스키마 점검 완료",
}


class EventIn(BaseModel):
    src: str = "CLIENT"
    level: str = "INFO"
    event: str = "EVENT"
    detail: str = ""
    robot_id: Optional[str] = None


# ============================================================
# 최근 이벤트 조회
# ============================================================
@router.get("/events/recent")
def get_recent_events(
    limit: int = Query(50, ge=1, le=500),
    robot_id: Optional[str] = None,
):
    """
    GUI가 기대하는 기본 키:
      id, ts, actor, level, event_type, message, robot_id

    + 클릭 상세용:
      detail_raw, detail_json
    """

    if robot_id:
        # 🔥 핵심: robot_id 컬럼 OR src 컬럼 둘 다 매칭 (기존 로그 대응)
        rows = db_fetchall(
            """
            SELECT
              id,
              created_at AS ts,
              src   AS actor,
              level,
              event AS event_type,
              detail,
              robot_id
            FROM event_log
            WHERE (robot_id = %s OR src = %s)
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (robot_id, robot_id, limit),
        )
    else:
        rows = db_fetchall(
            """
            SELECT
              id,
              created_at AS ts,
              src   AS actor,
              level,
              event AS event_type,
              detail,
              robot_id
            FROM event_log
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (limit,),
        )

    items = []

    for row in rows:
        # db_fetchall이 dict 반환한다고 가정
        detail_raw = row.get("detail")
        detail_json = None

        if detail_raw:
            try:
                detail_json = json.loads(detail_raw)
            except Exception:
                detail_json = None

        item = {
            "id": row.get("id"),
            "ts": row.get("ts"),
            "actor": row.get("actor"),
            "level": row.get("level"),
            "event_type": row.get("event_type"),
            "event_ko": EVENT_KO_MAP.get(str(row.get("event_type") or "").upper()),
            "message": detail_raw,
            "robot_id": row.get("robot_id") or row.get("actor"),
            # 👇 클릭 상세용
            "detail_raw": detail_raw,
            "detail_json": detail_json,
        }

        items.append(item)

    return {"ok": True, "items": items}


# ============================================================
# GUI 호환 alias (/api/events/recent)
# ============================================================
@router.get("/api/events/recent")
def api_recent_events(
    limit: int = Query(50, ge=1, le=500),
    robot_id: Optional[str] = None,
):
    return get_recent_events(limit=limit, robot_id=robot_id)


# ============================================================
# 외부 로그 기록 API
# ============================================================
@router.post("/api/events/log")
def post_event(e: EventIn):
    """
    외부 프로그램이 서버 event_log에 남기기 위한 API.
    robot_id 컬럼이 event_log에 존재하므로 이제는 robot_id도 함께 저장.
    """

    # 🔥 이제 detail에 박지 말고 robot_id 컬럼에 저장
    detail = e.detail

    log_event(
        src=e.src,
        level=e.level,
        event=e.event,
        detail=detail,
        robot_id=e.robot_id,  # 👈 중요
    )

    return {"ok": True}
