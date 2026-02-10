from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import db_execute, db_query_one
from ..log import log_event

router = APIRouter(prefix="/api/pinky", tags=["pinky"])


class PinkyStateIn(BaseModel):
    robot_id: str
    fsm_state: str = "IDLE"
    task_state: str = "NONE"

    goal_x: Optional[float] = None
    goal_y: Optional[float] = None
    goal_yaw: Optional[float] = None

    pose_x: Optional[float] = None
    pose_y: Optional[float] = None
    pose_yaw: Optional[float] = None

    battery_pct: Optional[float] = None

    dock_state: str = "UNKNOWN"
    docking_state: str = "NONE"

    # DB는 DATETIME이라도, 일단은 "없어도 됨"으로 운용 가능
    # 필요해지면 문자열/epoch 둘 다 받도록 확장 가능
    cooldown_until: Optional[Any] = None


class QueueCmdIn(BaseModel):
    robot_id: str
    command: str
    payload: str = ""
    args: Dict[str, Any] = Field(default_factory=dict)

    # ✅ 수동/자동 구분: src로 단순 처리
    # - 수동: GUI 또는 MANUAL
    # - 자동: AUTO
    src: str = "GUI"
    detail: str = ""
    is_auto: Optional[int] = None  # None이면 src로 결정


class AckIn(BaseModel):
    robot_id: str
    cmd_id: int
    status: str  # DONE / FAIL / CANCELED / IGNORED ...
    detail: str = ""


class ClearQueueIn(BaseModel):
    robot_id: str
    mode: str = "PENDING"  # PENDING or ALL


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _json_loads(s: Any) -> dict:
    if s is None:
        return {}
    if isinstance(s, dict):
        return s
    try:
        return json.loads(str(s))
    except Exception:
        return {}


@router.get("/state")
def get_state(robot_id: str):
    row = db_query_one("SELECT * FROM pinky_state_current WHERE robot_id=%s", (robot_id,))
    if not row:
        return {"ok": True, "state": None}

    for k in ("updated_at", "last_update", "cooldown_until"):
        if isinstance(row.get(k), datetime):
            row[k] = row[k].isoformat(timespec="microseconds")

    return {"ok": True, "state": row}


@router.post("/state")
def state_update(body: PinkyStateIn):
    now = datetime.now()
    updated_at = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    last_update = now.strftime("%Y-%m-%d %H:%M:%S")

    db_execute(
        """
        INSERT INTO pinky_state_current
        (robot_id, fsm_state, task_state,
         goal_x, goal_y, goal_yaw,
         pose_x, pose_y, pose_yaw,
         battery_pct, dock_state, docking_state,
         updated_at, last_update)
        VALUES (%s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s)
        ON DUPLICATE KEY UPDATE
          fsm_state=VALUES(fsm_state),
          task_state=VALUES(task_state),
          goal_x=VALUES(goal_x),
          goal_y=VALUES(goal_y),
          goal_yaw=VALUES(goal_yaw),
          pose_x=VALUES(pose_x),
          pose_y=VALUES(pose_y),
          pose_yaw=VALUES(pose_yaw),
          battery_pct=VALUES(battery_pct),
          dock_state=VALUES(dock_state),
          docking_state=VALUES(docking_state),
          updated_at=VALUES(updated_at),
          last_update=VALUES(last_update)
        """,
        (
            body.robot_id,
            body.fsm_state,
            body.task_state,
            body.goal_x,
            body.goal_y,
            body.goal_yaw,
            body.pose_x,
            body.pose_y,
            body.pose_yaw,
            body.battery_pct,
            body.dock_state,
            body.docking_state,
            updated_at,
            last_update,
        ),
    )
    return {"ok": True}


@router.post("/queue_command")
def queue_command(body: QueueCmdIn):
    payload = body.payload if body.payload is not None else ""
    detail = body.detail if body.detail is not None else ""
    args_json = _json_dumps(body.args or {})

    src = (body.src or "GUI").strip().upper()

    # ✅ is_auto 결정 규칙
    if body.is_auto is None:
        is_auto = 1 if src == "AUTO" else 0
    else:
        is_auto = 1 if int(body.is_auto) == 1 else 0

    db_execute(
        """
        INSERT INTO pinky_command_queue
        (robot_id, command, payload, status, detail, src, created_at, available_at, is_auto, args_json)
        VALUES (%s,%s,%s,'PENDING',%s,%s,NOW(6),NULL,%s,%s)
        """,
        (body.robot_id, body.command, payload, detail, src, is_auto, args_json),
    )

    log_event(body.robot_id, "INFO", "PINKY_CMD_ENQ", f"{body.command} src={src} is_auto={is_auto}")
    return {"ok": True}


@router.post("/ack")
def ack(body: AckIn):
    status = (body.status or "DONE").upper()
    detail = body.detail if body.detail is not None else ""

    db_execute(
        """
        UPDATE pinky_command_queue
        SET status=%s, detail=%s, done_at=NOW(6)
        WHERE id=%s AND robot_id=%s
        """,
        (status, detail, body.cmd_id, body.robot_id),
    )
    return {"ok": True}


@router.get("/next_command")
def next_command(robot_id: str):
    row = db_query_one(
        """
        SELECT id, command, payload, detail, src, is_auto, args_json, available_at
        FROM pinky_command_queue
        WHERE robot_id=%s
          AND status='PENDING'
          AND (available_at IS NULL OR available_at <= NOW(6))
        ORDER BY
          (available_at IS NULL) DESC,
          available_at ASC,
          created_at ASC,
          id ASC
        LIMIT 1
        """,
        (robot_id,),
    )

    if not row:
        return {"ok": True, "cmd": None}

    cmd_id = int(row["id"])
    cmd = str(row["command"])
    args = _json_loads(row.get("args_json"))
    payload = row.get("payload", "")
    detail = row.get("detail", "")
    src = row.get("src", "")
    is_auto = int(row.get("is_auto") or 0)

    db_execute(
        """
        UPDATE pinky_command_queue
        SET status='RUNNING', claimed_at=NOW(6)
        WHERE id=%s AND robot_id=%s
        """,
        (cmd_id, robot_id),
    )

    return {
        "ok": True,
        "cmd": cmd,
        "cmd_id": cmd_id,
        "args": args,
        "payload": payload,
        "detail": detail,
        "src": src,
        "is_auto": is_auto,
    }
