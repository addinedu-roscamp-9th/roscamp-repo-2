from __future__ import annotations

from datetime import datetime
import os
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from server_core.db import db_execute, db_fetchone, _get_primary_key_column
from server_core.log import log_event

router = APIRouter()


def _effective_client_id(client_id: Optional[str], robot_id: Optional[str]) -> str:
    """
    호환 정책:
    - robot_id가 오면 그걸 우선 사용 (GUI/클라이언트들이 robot_id로 보내는 경우 대비)
    - 없으면 client_id 사용
    - 둘 다 없으면 기본 jetcobot1
    """
    rid = (robot_id or "").strip()
    cid = (client_id or "").strip()
    return rid or cid or "jetcobot1"


class ArmStateReport(BaseModel):
    # 호환: client_id / robot_id 둘 다 받음
    client_id: str = "jetcobot1"
    robot_id: Optional[str] = None

    state: str = "READY"
    job: str = "NONE"
    warn: str = "--"
    detected: Optional[bool] = None
    src: str = "ARM"


def _ensure_arm_state_row(client_id: str) -> None:
    row = db_fetchone("SELECT client_id FROM arm_state_current WHERE client_id=%s", (client_id,))
    if row is None:
        db_execute(
            """
            INSERT INTO arm_state_current(client_id,state,job,detected,warn,updated_at)
            VALUES(%s,'READY','NONE',0,'--',%s)
            """,
            (client_id, datetime.now()),
        )


def _arm_enable_check(client_id: str) -> None:
    _ensure_arm_state_row(client_id)


def _pk_col(table: str) -> str:
    return _get_primary_key_column(table) or "id"


def _extract_target_robot_id(text: str) -> Optional[str]:
    """
    START_CHARGE detail에서 대상 핑키(robot_id)를 뽑습니다.
    지원 패턴:
      - "... robot_id=pinky2 ..."
      - "Completed: pinky2"
      - "Completed: pinky2 something" (첫 토큰)
    """
    s = (text or "").strip()
    if not s:
        return None

    m = re.search(r"\brobot_id\s*=\s*([A-Za-z0-9_\-]+)\b", s)
    if m:
        return m.group(1)

    m = re.search(r"\bCompleted:\s*([A-Za-z0-9_\-]+)\b", s)
    if m:
        return m.group(1)

    return None


def _pinky_enqueue_after_charge(robot_id: str, src: str, reason: str) -> Optional[int]:
    """
    pinky_command_queue에 AFTER_CHARGE를 넣되,
    이미 PENDING/RUNNING이 있으면 중복 생성하지 않습니다.
    """
    rid = (robot_id or "").strip()
    if not rid:
        return None

    row_exist = db_fetchone(
        """
        SELECT id
        FROM pinky_command_queue
        WHERE robot_id=%s
          AND command='AFTER_CHARGE'
          AND status IN ('PENDING','RUNNING')
        ORDER BY id DESC
        LIMIT 1
        """,
        (rid,),
    )
    if row_exist is not None:
        try:
            keep_id = int(row_exist["id"])
        except Exception:
            keep_id = None

        log_event(
            rid,
            "INFO",
            "PINKY_CMD_ENQ_SUPPRESS",
            f"AFTER_CHARGE suppressed (active exists) keep_cmd_id={keep_id}",
        )
        return keep_id

    # 최신 서버 스키마( payload/available_at 없음 ) 기준 insert
    db_execute(
        """
        INSERT INTO pinky_command_queue
        (robot_id, command, status, detail, src, created_at, claimed_at, done_at,
         is_auto, args_json, result_detail)
        VALUES
        (%s, 'AFTER_CHARGE', 'PENDING', %s, %s, NOW(6), NULL, NULL,
         1, %s, '')
        """,
        (rid, reason[:255], (src or "AUTO")[:32], "{}"),
    )

    row_new = db_fetchone(
        """
        SELECT id
        FROM pinky_command_queue
        WHERE robot_id=%s
        ORDER BY id DESC
        LIMIT 1
        """,
        (rid,),
    )
    if not row_new:
        return None
    try:
        return int(row_new["id"])
    except Exception:
        return None


@router.get("/api/arm/state")
def arm_state(
    # ✅ 둘 다 허용
    client_id: Optional[str] = Query(None),
    robot_id: Optional[str] = Query(None),
):
    cid = _effective_client_id(client_id, robot_id)
    _arm_enable_check(cid)

    row = db_fetchone(
        """
        SELECT client_id,state,job,detected,warn,updated_at
        FROM arm_state_current
        WHERE client_id=%s
        """,
        (cid,),
    )

    if row is None:
        return {
            "ok": True,
            "state": {
                "client_id": cid,
                "state": "READY",
                "job": "NONE",
                "detected": False,
                "warn": "--",
                "updated_at": None,
            },
        }

    return {
        "ok": True,
        "state": {
            "client_id": row["client_id"],
            "state": row["state"],
            "job": row["job"],
            "detected": bool(row["detected"]),
            "warn": row["warn"],
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        },
    }


@router.post("/api/arm/report_state")
def arm_report_state(report: ArmStateReport, request: Request):
    """
    ✅ 목적:
    - arm_state_current.updated_at은 계속 갱신(ONLINE 유지용)
    - event_log는 스팸 금지
      -> warn 전환(SET/CLEARED) 때만 1개씩 남김
      -> ARM_STATE 같은 heartbeat 로그는 남기지 않음
    """
    cid = _effective_client_id(report.client_id, report.robot_id)
    _arm_enable_check(cid)

    detected = report.detected if report.detected is not None else False
    new_warn = (str(report.warn)[:255] if report.warn is not None else "--").strip() or "--"

    # 이전 warn 읽기 (전환 감지)
    prev = db_fetchone("SELECT warn FROM arm_state_current WHERE client_id=%s", (cid,))
    prev_warn = (str(prev.get("warn", "--")) if prev else "--").strip() or "--"

    # ✅ 상태는 항상 업데이트(heartbeat)
    db_execute(
        """
        UPDATE arm_state_current
        SET state=%s, job=%s, detected=%s, warn=%s, updated_at=%s
        WHERE client_id=%s
        """,
        (report.state, report.job, 1 if detected else 0, new_warn, datetime.now(), cid),
    )

    # ✅ warn 변화가 있을 때만 이벤트 로그 1회
    if new_warn != prev_warn:
        ip = getattr(request.client, "host", "?")

        if new_warn != "--":
            # warn 새로 생김 / warn 내용이 바뀜
            log_event(
                report.src,
                "WARN",
                "ARM_WARN_SET",
                f"ip={ip} client_id={cid} warn={new_warn} (prev={prev_warn}) state={report.state} job={report.job} detected={detected}",
            )
        else:
            # warn 해제
            log_event(
                report.src,
                "INFO",
                "ARM_WARN_CLEARED",
                f"ip={ip} client_id={cid} prev_warn={prev_warn} state={report.state} job={report.job} detected={detected}",
            )

    return {"ok": True}


@router.post("/api/arm/set_detected")
def arm_set_detected(payload: Dict[str, Any], request: Request):
    # ✅ 호환 키들: client_id / robot_id
    cid = _effective_client_id(
        str(payload.get("client_id", "") or ""),
        str(payload.get("robot_id", "") or ""),
    )

    detected = bool(payload.get("detected", False))
    src = str(payload.get("src", "LIVE"))
    conf = payload.get("conf", None)

    _arm_enable_check(cid)

    try:
        conf_s = f"{float(conf):.2f}" if conf is not None else "--"
    except Exception:
        conf_s = "--"

    db_execute(
        """
        UPDATE arm_state_current
        SET detected=%s, warn=%s, updated_at=%s
        WHERE client_id=%s
        """,
        (1 if detected else 0, f"conf={conf_s}", datetime.now(), cid),
    )

    ip = getattr(request.client, "host", "?")
    log_event(src, "INFO", "ARM_DETECTED", f"ip={ip} client_id={cid} detected={detected} conf={conf_s}")
    return {"ok": True}


@router.post("/api/arm/queue_command")
def arm_queue_command(payload: Dict[str, Any], request: Request):
    cid = _effective_client_id(
        str(payload.get("client_id", "") or ""),
        str(payload.get("robot_id", "") or ""),
    )

    command = str(payload.get("command", "")).strip().upper()
    src = str(payload.get("src", "GUI"))
    detail = str(payload.get("detail", ""))[:255]

    if not command:
        return {"ok": False, "error": "empty command"}

    if command in ("CAM_START", "CAM_STOP"):
        allow = os.getenv("TASHO_ALLOW_CAM_COMMANDS", "0").strip() in ("1", "true", "True", "ON", "on")
        if not allow:
            ip = getattr(request.client, "host", "?")
            log_event(src, "INFO", "ARM_CMD_DROPPED", f"ip={ip} client_id={cid} cmd={command} detail=dropped (unsupported)")
            return {"ok": True, "dropped": True}

    # ✅ 중복 방지: START_CHARGE는 같은 대상(robot_id=...)이면 PENDING/RUNNING이 있을 때 억제
    if command == "START_CHARGE":
        target_robot = _extract_target_robot_id(detail)
        if target_robot:
            row_exist = db_fetchone(
                """
                SELECT id
                FROM arm_command_queue
                WHERE client_id=%s
                  AND command=%s
                  AND status IN ('PENDING','CLAIMED')
                  AND detail LIKE %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (cid, "START_CHARGE", f"%robot_id={target_robot}%"),
            )
            if row_exist is not None:
                try:
                    keep_id = int(row_exist["id"])
                except Exception:
                    keep_id = None
                ip = getattr(request.client, "host", "?")
                log_event(src, "INFO", "ARM_CMD_ENQ_SUPPRESS", f"ip={ip} client_id={cid} cmd=START_CHARGE suppressed keep_cmd_id={keep_id} robot_id={target_robot}")
                return {"ok": True, "suppressed": True, "keep_cmd_id": keep_id}

    db_execute(
        """
        INSERT INTO arm_command_queue(client_id,command,status,detail,src,created_at,claimed_at,done_at)
        VALUES(%s,%s,'PENDING',%s,%s,%s,NULL,NULL)
        """,
        (cid, command, detail, src, datetime.now()),
    )

    ip = getattr(request.client, "host", "?")
    log_event(src, "INFO", "ARM_CMD_QUEUED", f"ip={ip} client_id={cid} cmd={command} detail={detail}")
    return {"ok": True}


@router.get("/api/arm/next_command")
def arm_next_command(
    client_id: Optional[str] = Query(None),
    robot_id: Optional[str] = Query(None),
    request: Request = None,
):
    cid = _effective_client_id(client_id, robot_id)

    row = db_fetchone(
        """
        SELECT id, command, detail, src, created_at
        FROM arm_command_queue
        WHERE client_id=%s AND status='PENDING'
        ORDER BY id ASC
        LIMIT 1
        """,
        (cid,),
    )

    if row is None:
        return {"cmd": None}

    cmd_id = int(row["id"])
    cmd = str(row["command"])
    detail = str(row.get("detail") or "")
    src = str(row.get("src") or "")

    db_execute(
        """
        UPDATE arm_command_queue
        SET status='CLAIMED', claimed_at=%s
        WHERE id=%s
        """,
        (datetime.now(), cmd_id),
    )

    ip = getattr(getattr(request, "client", None), "host", "?") if request is not None else "?"
    log_event("SERVER", "INFO", "ARM_CMD_CLAIMED", f"ip={ip} client_id={cid} cmd_id={cmd_id} cmd={cmd} detail={detail}")
    return {"cmd": cmd, "cmd_id": cmd_id, "detail": detail, "src": src}


@router.post("/api/arm/ack_command")
def arm_ack_command(payload: Dict[str, Any], request: Request):
    cmd_id = payload.get("id", None)
    if cmd_id is None:
        cmd_id = payload.get("cmd_id", None)

    cid = _effective_client_id(
        str(payload.get("client_id", "") or ""),
        str(payload.get("robot_id", "") or ""),
    )

    status = str(payload.get("status", "DONE")).upper()
    detail = str(payload.get("detail", ""))[:255]

    if cmd_id is None:
        return {"ok": False, "error": "missing id/cmd_id"}

    try:
        cmd_id = int(cmd_id)
    except Exception:
        return {"ok": False, "error": "bad id"}

    if status not in ("DONE", "FAILED", "IGNORED"):
        status = "DONE"

    # ✅ 어떤 command였는지 / 기존 detail이 뭐였는지 먼저 읽음(오토시퀀스용)
    row_before = db_fetchone(
        """
        SELECT command, detail
        FROM arm_command_queue
        WHERE id=%s AND client_id=%s
        """,
        (cmd_id, cid),
    )
    cmd_name = str((row_before or {}).get("command") or "").strip().upper()
    prev_detail = str((row_before or {}).get("detail") or "")

    db_execute(
        """
        UPDATE arm_command_queue
        SET status=%s, done_at=%s, detail=%s
        WHERE id=%s AND client_id=%s
        """,
        (status, datetime.now(), detail, cmd_id, cid),
    )

    ip = getattr(request.client, "host", "?")
    log_event(
        "ARM",
        "WARNING" if status in ("FAILED", "IGNORED") else "INFO",
        "ARM_CMD_ACK",
        f"ip={ip} id={cmd_id} client_id={cid} status={status} detail={detail}",
    )

    # ✅ AUTOSEQ: ARM START_CHARGE DONE -> Pinky AFTER_CHARGE enqueue
    if status == "DONE" and cmd_name == "START_CHARGE":
        target_robot = _extract_target_robot_id(prev_detail) or _extract_target_robot_id(detail)
        if target_robot:
            new_id = _pinky_enqueue_after_charge(
                robot_id=target_robot,
                src="AUTO",
                reason=f"AUTOSEQ after ARM START_CHARGE DONE -> AFTER_CHARGE (arm_cmd_id={cmd_id})",
            )
            log_event(
                target_robot,
                "INFO",
                "PINKY_CMD_ENQ",
                f"AFTER_CHARGE src=AUTO detail=AUTOSEQ after ARM START_CHARGE DONE -> AFTER_CHARGE cmd_id={new_id}",
            )
        else:
            log_event(
                "SERVER",
                "WARNING",
                "AUTOSEQ_AFTER_CHARGE_SKIPPED",
                f"arm_cmd_id={cmd_id} client_id={cid} reason=no_target_robot_id prev_detail={prev_detail} ack_detail={detail}",
            )

    return {"ok": True}


@router.post("/api/arm/ack")
def arm_ack_compat(payload: Dict[str, Any], request: Request):
    return arm_ack_command(payload, request)
