from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Query

from server_core.db import db_execute, db_fetchone, _get_primary_key_column
from server_core.log import log_event

router = APIRouter()

@router.get("/api/arm/state")
def get_arm_state(client_id: str = Query("jetcobot1")):
    row = db_fetchone(
        """
        SELECT state, job, detected, warn, updated_at
        FROM arm_state_current
        WHERE client_id=%s
        """,
        (client_id,),
    )
    if row is None:
        db_execute(
            """
            INSERT INTO arm_state_current(client_id,state,job,detected,warn,updated_at)
            VALUES(%s,'READY','NONE',0,'--',%s)
            """,
            (client_id, datetime.now()),
        )
        row = db_fetchone(
            "SELECT state, job, detected, warn, updated_at FROM arm_state_current WHERE client_id=%s",
            (client_id,),
        )

    ua = row["updated_at"]
    if isinstance(ua, datetime):
        ua = ua.isoformat(timespec="microseconds")

    return {
        "state": row["state"],
        "job": row["job"],
        "detected": bool(row["detected"]),
        "warn": row["warn"],
        "updated_at": ua,
    }

@router.post("/api/arm/set_detected")
def set_arm_detected(payload: Dict[str, Any]):
    client_id = str(payload.get("client_id", "jetcobot1"))
    detected = bool(payload.get("detected", False))
    src = str(payload.get("src", "LIVE"))
    conf = payload.get("conf", None)
    try:
        conf_s = f"{float(conf):.2f}" if conf is not None else "--"
    except Exception:
        conf_s = "--"

    row = db_fetchone("SELECT client_id FROM arm_state_current WHERE client_id=%s", (client_id,))
    if row is None:
        db_execute(
            """
            INSERT INTO arm_state_current(client_id,state,job,detected,warn,updated_at)
            VALUES(%s,'READY','NONE',%s,%s,%s)
            """,
            (client_id, 1 if detected else 0, f"conf={conf_s}", datetime.now()),
        )
    else:
        db_execute(
            """
            UPDATE arm_state_current
            SET detected=%s, warn=%s, updated_at=%s
            WHERE client_id=%s
            """,
            (1 if detected else 0, f"conf={conf_s}", datetime.now(), client_id),
        )

    log_event(src, "INFO", "ARM_DETECTED", f"client_id={client_id} detected={detected} conf={conf_s}")
    return {"ok": True}

@router.post("/api/arm/queue_command")
def arm_queue_command(payload: Dict[str, Any]):
    client_id = str(payload.get("client_id", "jetcobot1"))
    command = str(payload.get("command", "")).strip().upper()
    src = str(payload.get("src", "GUI"))

    if not command:
        return {"ok": False, "error": "empty command"}

    db_execute(
        """
        INSERT INTO arm_command_queue(client_id,command,status,detail,src,created_at,claimed_at,done_at)
        VALUES(%s,%s,'PENDING','',%s,%s,NULL,NULL)
        """,
        (client_id, command, src, datetime.now()),
    )
    log_event(src, "INFO", "ARM_CMD_QUEUED", f"client_id={client_id} cmd={command}")
    return {"ok": True}

@router.get("/api/arm/next_command")
def arm_next_command(client_id: str = Query("jetcobot1")):
    row = db_fetchone(
        """
        SELECT id, command
        FROM arm_command_queue
        WHERE client_id=%s AND status='PENDING'
        ORDER BY id ASC
        LIMIT 1
        """,
        (client_id,),
    )
    if row is None:
        return {"cmd": None}

    cmd_id = int(row["id"])
    cmd = str(row["command"])

    db_execute(
        """
        UPDATE arm_command_queue
        SET status='CLAIMED', claimed_at=%s
        WHERE id=%s AND status='PENDING'
        """,
        (datetime.now(), cmd_id),
    )
    log_event("SERVER", "INFO", "ARM_CMD_CLAIMED", f"client_id={client_id} cmd_id={cmd_id} cmd={cmd}")
    return {"cmd": cmd, "cmd_id": cmd_id}

@router.post("/api/arm/ack")
def arm_ack(payload: Dict[str, Any]):
    client_id = str(payload.get("client_id", "jetcobot1"))
    cmd_id = payload.get("cmd_id", None)
    status = str(payload.get("status", "DONE")).strip().upper()
    detail = str(payload.get("detail", ""))[:255]
    src = str(payload.get("src", "ARM"))

    pk_col = _get_primary_key_column("arm_command_queue") or "id"
    try:
        cmd_id_i = int(cmd_id) if cmd_id is not None else None
    except Exception:
        cmd_id_i = None

    if cmd_id_i is None:
        log_event(src, "WARN", "ARM_ACK_NO_ID", f"client_id={client_id} status={status} detail={detail}")
        return {"ok": False, "error": "missing cmd_id"}

    db_execute(
        f"""
        UPDATE arm_command_queue
        SET status=%s, detail=%s, done_at=%s
        WHERE {pk_col}=%s
        """,
        (status, detail, datetime.now(), cmd_id_i),
    )

    level = "INFO" if status == "DONE" else ("WARN" if status == "IGNORED" else "ERROR")
    log_event(src, level, "ARM_CMD_ACK", f"{pk_col}={cmd_id_i} client_id={client_id} status={status} detail={detail}")
    return {"ok": True}
