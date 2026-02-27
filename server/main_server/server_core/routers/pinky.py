from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from ..db import db_execute, db_fetchone, db_query_one
from ..log import log_event

router = APIRouter()

ARM_CLIENT_ID = "jetcobot1"
ARM_CHARGE_COMMAND = "START_CHARGE"


def _json_loads_safe(s: Any) -> Dict[str, Any]:
    if not s:
        return {}
    try:
        if isinstance(s, (dict, list)):
            return s  # type: ignore[return-value]
        return json.loads(str(s))
    except Exception:
        return {}


class PinkyStateBody(BaseModel):
    robot_id: str = Field(..., description="pinky1 / pinky2")
    fsm_state: str = Field("UNKNOWN")
    task_state: str = Field("NONE")
    goal_x: Optional[float] = None
    goal_y: Optional[float] = None
    goal_yaw: Optional[float] = None
    pose_x: Optional[float] = None
    pose_y: Optional[float] = None
    pose_yaw: Optional[float] = None
    battery_pct: Optional[float] = None
    dock_state: str = Field("UNKNOWN")
    docking_state: str = Field("NONE")
    cooldown_until: Optional[str] = None


class QueueCmdBody(BaseModel):
    robot_id: str
    command: str
    src: str = "USER_GUI"
    detail: str = ""
    args: Dict[str, Any] = Field(default_factory=dict)


class AckBody(BaseModel):
    robot_id: str
    cmd_id: int
    status: str = "DONE"
    detail: str = ""
    src: str = "PINKY"
    # supervisor가 result_detail을 보내도 받고 싶으면 유지
    result_detail: str = ""


def _get_current_queue_command(robot_id: str) -> Optional[Dict[str, Any]]:
    row = db_fetchone(
        """
        SELECT id, command, status, detail
        FROM pinky_command_queue
        WHERE robot_id=%s
          AND status IN ('RUNNING','PENDING')
        ORDER BY
          CASE
            WHEN status='RUNNING' THEN 0
            WHEN status='PENDING' THEN 1
            ELSE 2
          END,
          id ASC
        LIMIT 1
        """,
        (robot_id,),
    )
    if not row:
        return None

    cmd = str(row.get("command") or "").strip().upper()
    st = str(row.get("status") or "").strip().upper()
    label = f"{st}: {cmd}" if cmd else st

    return {
        "id": int(row.get("id") or 0),
        "command": cmd,
        "status": st,
        "detail": str(row.get("detail") or ""),
        "label": label,
    }


def _find_active_cmd_id(robot_id: str, command: str) -> Optional[int]:
    row = db_fetchone(
        """
        SELECT id
        FROM pinky_command_queue
        WHERE robot_id=%s
          AND command=%s
          AND status IN ('PENDING','RUNNING')
        ORDER BY id DESC
        LIMIT 1
        """,
        (robot_id, command),
    )
    if not row:
        return None
    try:
        return int(row["id"])
    except Exception:
        return None


def _enqueue_internal(
    robot_id: str,
    command: str,
    src: str,
    detail: str,
    args: Dict[str, Any] | None = None,
    is_auto: int = 0,
    dedupe_active: bool = False,
) -> int:
    """
    dedupe_active=True:
      동일 robot_id+command가 PENDING/RUNNING이면 새로 insert하지 않고 기존 id 반환.
    """
    args = args or {}
    command_u = (command or "").strip().upper()
    src_u = (src or "").strip()

    if dedupe_active:
        exist = _find_active_cmd_id(robot_id, command_u)
        if exist is not None:
            log_event(
                src=robot_id,
                level="INFO",
                event="PINKY_CMD_ENQ_SUPPRESS",
                detail=f"{command_u} suppressed (active exists) src={src_u} keep_cmd_id={exist}",
            )
            return exist

    args_json = json.dumps(args, ensure_ascii=False)

    db_execute(
        """
        INSERT INTO pinky_command_queue
        (robot_id, command, status, detail, src, created_at, claimed_at, done_at,
         is_auto, args_json, result_detail)
        VALUES
        (%s, %s, 'PENDING', %s, %s, NOW(6), NULL, NULL,
         %s, %s, '')
        """,
        (robot_id, command_u, detail[:255], src_u[:32], int(is_auto), args_json),
    )

    row = db_query_one(
        """
        SELECT id
        FROM pinky_command_queue
        WHERE robot_id=%s
        ORDER BY id DESC
        LIMIT 1
        """,
        (robot_id,),
    )
    return int(row["id"]) if row else -1


@router.post("/state")
def post_state(body: PinkyStateBody = Body(...)):
    db_execute(
        """
        INSERT INTO pinky_state_current
        (robot_id, fsm_state, task_state,
         goal_x, goal_y, goal_yaw,
         pose_x, pose_y, pose_yaw,
         battery_pct, dock_state, docking_state,
         updated_at, last_update, cooldown_until)
        VALUES
        (%s, %s, %s,
         %s, %s, %s,
         %s, %s, %s,
         %s, %s, %s,
         NOW(6), NOW(6), %s)
        ON DUPLICATE KEY UPDATE
          fsm_state=VALUES(fsm_state),
          task_state=VALUES(task_state),
          goal_x=VALUES(goal_x),
          goal_y=VALUES(goal_y),
          goal_yaw=VALUES(goal_yaw),
          pose_x=VALUES(pose_x),
          pose_y=VALUES(pose_y),
          pose_y=VALUES(pose_y),
          pose_yaw=VALUES(pose_yaw),
          battery_pct=VALUES(battery_pct),
          dock_state=VALUES(dock_state),
          docking_state=VALUES(docking_state),
          updated_at=NOW(6),
          last_update=NOW(6),
          cooldown_until=VALUES(cooldown_until)
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
            body.cooldown_until,
        ),
    )
    return {"ok": True}


@router.get("/state")
def get_state(robot_id: str = Query(...)):
    current_cmd = _get_current_queue_command(robot_id)

    row = db_fetchone(
        """
        SELECT
          robot_id, fsm_state, task_state,
          goal_x, goal_y, goal_yaw,
          pose_x, pose_y, pose_yaw,
          battery_pct, dock_state, docking_state,
          updated_at, last_update, cooldown_until
        FROM pinky_state_current
        WHERE robot_id=%s
        """,
        (robot_id,),
    )
    if row is None:
        fallback_event = (current_cmd or {}).get("label", "--")
        return {
            "ok": True,
            "state": {
                "robot_id": robot_id,
                "fsm_state": "UNKNOWN",
                "task_state": "NONE",
                "current_command": current_cmd,
                "current_event": fallback_event,
            },
        }

    state = dict(row)
    fallback_event = (current_cmd or {}).get("label")
    if not fallback_event:
        fsm = str(state.get("fsm_state") or "").strip().upper()
        task = str(state.get("task_state") or "").strip().upper()
        if task and task != "NONE":
            fallback_event = f"{fsm}: {task}" if fsm else task
        elif fsm:
            fallback_event = fsm
        else:
            fallback_event = "--"
    state["current_command"] = current_cmd
    state["current_event"] = fallback_event
    return {"ok": True, "state": state}


@router.post("/queue_command")
def queue_command(body: QueueCmdBody = Body(...)):
    cmd = (body.command or "").strip().upper()
    src = (body.src or "USER_GUI")[:32]

    # ✅ DOCKING 중복 enqueue 차단 (SUPERVISOR/USER_GUI 둘 다 보호)
    dedupe = cmd in ("DOCKING",)

    cmd_id = _enqueue_internal(
        robot_id=body.robot_id,
        command=cmd,
        src=src,
        detail=(body.detail or "")[:255],
        args=body.args or {},
        is_auto=0,
        dedupe_active=dedupe,
    )
    return {"ok": True, "cmd_id": cmd_id}


@router.get("/next_command")
def next_command(robot_id: str = Query(...), only_manual: int = Query(0)):
    # NOTE:
    # - DOCKING은 '서버 트리거/로그' 성격이며, 실제 실행 명령은 Supervisor가 직접 노드를 실행합니다.
    # - 따라서 /next_command 로직에서 DOCKING을 꺼내주면 다른 프로세스가 가져가 중복 실행될 수 있어,
    #   항상 제외합니다.
    extra_where = """
      AND UPPER(command) <> 'DOCKING'
    """
    if int(only_manual or 0) == 1:
        extra_where += """
          AND is_auto=0
          AND UPPER(src) NOT IN ('SUPERVISOR','AUTO')
        """

    row = db_fetchone(
        f"""
        SELECT id, command, args_json, src, is_auto
        FROM pinky_command_queue
        WHERE robot_id=%s AND status='PENDING'
        {extra_where}
        ORDER BY id ASC
        LIMIT 1
        """,
        (robot_id,),
    )
    if row is None:
        return {"cmd": None}

    cmd_id = int(row["id"])
    cmd = str(row["command"])
    args = _json_loads_safe(row.get("args_json"))

    db_execute(
        """
        UPDATE pinky_command_queue
        SET status='RUNNING', claimed_at=NOW(6)
        WHERE id=%s AND robot_id=%s AND status='PENDING'
        """,
        (cmd_id, robot_id),
    )

    log_event(
        src=robot_id,
        level="INFO",
        event="PINKY_CMD_CLAIMED",
        detail=f"cmd_id={cmd_id} cmd={cmd} args={args}",
    )

    return {"cmd": cmd, "cmd_id": cmd_id, "args": args, "src": row.get("src"), "is_auto": int(row.get("is_auto") or 0)}


@router.post("/ack")
@router.post("/ack_command")
def ack_command(body: AckBody = Body(...)):
    status = (body.status or "DONE").strip().upper()
    if status not in ("DONE", "FAILED", "IGNORED"):
        status = "DONE"

    # cmd_name 조회(오토시퀀스 분기용)
    row_cmd = db_fetchone(
        "SELECT command FROM pinky_command_queue WHERE id=%s AND robot_id=%s",
        (int(body.cmd_id), body.robot_id),
    )
    cmd_name = (row_cmd.get("command") if row_cmd else "") or ""
    cmd_name = str(cmd_name).strip().upper()

    db_execute(
        """
        UPDATE pinky_command_queue
        SET status=%s, done_at=NOW(6), result_detail=%s
        WHERE id=%s AND robot_id=%s
        """,
        (status, (body.result_detail or body.detail or "")[:255], int(body.cmd_id), body.robot_id),
    )

    log_event(
        src=body.robot_id,
        level="INFO",
        event="PINKY_CMD_ACK",
        detail=f"cmd_id={int(body.cmd_id)} status={status} detail={(body.detail or '')[:255]}",
    )

    # -----------------------------
    # AUTOSEQ 1) DOCKING DONE -> CHARGE (배터리 < 80)
    # ✅ 중복 차단: CHARGE가 이미 PENDING/RUNNING이면 새로 enqueue하지 않음
    # -----------------------------
    if status == "DONE" and cmd_name == "DOCKING":
        row_state = db_fetchone(
            "SELECT battery_pct FROM pinky_state_current WHERE robot_id=%s",
            (body.robot_id,),
        )
        batt = row_state.get("battery_pct") if row_state else None
        try:
            batt_f = float(batt) if batt is not None else None
        except Exception:
            batt_f = None

        if batt_f is not None and batt_f < 80.0:
            exist_charge = _find_active_cmd_id(body.robot_id, "CHARGE")
            if exist_charge is None:
                cmd_id2 = _enqueue_internal(
                    robot_id=body.robot_id,
                    command="CHARGE",
                    src="AUTO",
                    detail=f"AUTOSEQ after DOCKING: battery {int(batt_f)}% < 80% -> CHARGE",
                    args={},
                    is_auto=1,
                    dedupe_active=True,
                )
                log_event(
                    src=body.robot_id,
                    level="INFO",
                    event="PINKY_CMD_ENQ",
                    detail=f"CHARGE src=AUTO detail=AUTOSEQ after DOCKING: battery {int(batt_f)}% < 80% -> CHARGE cmd_id={cmd_id2}",
                )

    # -----------------------------
    # AUTOSEQ 2) CHARGE DONE -> ARM START_CHARGE
    # ✅ 중복 차단: arm_command_queue에 동일 로봇 대상으로 PENDING/RUNNING/CLAIMED가 있으면 enqueue하지 않음
    # -----------------------------
    if status == "DONE" and cmd_name == "CHARGE":
        # detail에 robot_id=...를 넣는 현재 설계를 그대로 활용해서 중복 체크
        row_exist = db_fetchone(
            """
            SELECT id
            FROM arm_command_queue
            WHERE client_id=%s
              AND command=%s
              AND status IN ('PENDING','RUNNING','CLAIMED')
              AND detail LIKE %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (ARM_CLIENT_ID, ARM_CHARGE_COMMAND, f"%robot_id={body.robot_id}%"),
        )
        if row_exist is None:
            db_execute(
                """
                INSERT INTO arm_command_queue
                (client_id, command, status, detail, src, created_at, claimed_at, done_at)
                VALUES
                (%s, %s, 'PENDING', %s, 'SERVER', NOW(6), NULL, NULL)
                """,
                (
                    ARM_CLIENT_ID,
                    ARM_CHARGE_COMMAND,
                    f"{ARM_CHARGE_COMMAND} -> {ARM_CLIENT_ID} (robot_id={body.robot_id} | AUTOSEQ after CHARGE DONE -> ARM {ARM_CHARGE_COMMAND})",
                ),
            )
            log_event(
                src=body.robot_id,
                level="INFO",
                event="ARM_CMD_ENQ",
                detail=f"{ARM_CHARGE_COMMAND} -> {ARM_CLIENT_ID} (robot_id={body.robot_id} | AUTOSEQ after CHARGE DONE -> ARM {ARM_CHARGE_COMMAND})",
            )
        else:
            log_event(
                src=body.robot_id,
                level="INFO",
                event="ARM_CMD_ENQ_SUPPRESS",
                detail=f"ARM {ARM_CHARGE_COMMAND} suppressed (active exists) keep_cmd_id={int(row_exist['id'])}",
            )

    return {"ok": True}
