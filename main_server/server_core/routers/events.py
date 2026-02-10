from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from server_core.db import db_fetchall

router = APIRouter(tags=["events"])


@router.get("/events/recent")
def get_recent_events(limit: int = Query(50, ge=1, le=500), robot_id: Optional[str] = None):
    """
    GUI/클라이언트가 기대하는 키:
      id, ts, actor, level, event_type, message, robot_id
    DB 실제 컬럼:
      created_at, src, level, event, detail
    """
    if robot_id:
        items = db_fetchall(
            """
            SELECT
              id,
              created_at AS ts,
              src   AS actor,
              level,
              event AS event_type,
              detail AS message,
              %s AS robot_id
            FROM event_log
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (robot_id, limit),
        )
    else:
        items = db_fetchall(
            """
            SELECT
              id,
              created_at AS ts,
              src   AS actor,
              level,
              event AS event_type,
              detail AS message,
              NULL AS robot_id
            FROM event_log
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
    return {"ok": True, "items": items}
