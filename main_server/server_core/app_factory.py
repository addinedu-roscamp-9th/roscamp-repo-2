from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .schema import ensure_schema
from .log import log_event, get_logger
from .db import db_execute, db_query_all, db_query_one
from .routers.health import router as health_router
from .routers.pinky import router as pinky_router
from .routers.events import router as events_router

# arm router is optional; import only if present
try:
    from .routers.arm import router as arm_router  # type: ignore
except Exception:
    arm_router = None


# =========================
# AUTO POLICY (서버 자동판단)
# =========================
AUTO_POLL_SEC = 1.0
AUTO_TRIGGER_SEC = 6.0        # 쿨다운 7초 기준 "체감 6초"에 자동 enqueue
AUTO_COOLDOWN_SEC = 7.0       # 핑키 supervisor 쿨다운과 동일하게 유지
AUTO_BATTERY_THRESHOLD = 30.0 # 30% 미만이면 CHARGE


def _best_effort_host_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def _dt_now() -> datetime:
    return datetime.now()


def _secs(dt: datetime) -> float:
    return dt.timestamp()


def _enqueue_auto(robot_id: str, command: str, args: Dict[str, Any] | None = None, detail: str = "") -> None:
    args = args or {}
    payload = "{}"
    args_json = "{}"
    try:
        import json
        args_json = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        args_json = "{}"

    db_execute(
        """
        INSERT INTO pinky_command_queue
        (robot_id, command, payload, status, detail, src, created_at, available_at, is_auto, args_json)
        VALUES (%s,%s,%s,'PENDING',%s,'AUTO',NOW(6),NULL,1,%s)
        """,
        (robot_id, command, payload, (detail or "")[:255], args_json),
    )

    # 터미널은 짧고 한국어로
    log_event(robot_id, "INFO", "AUTO_ENQ", f"자동명령 큐추가: {command} | {detail}".strip())


async def _auto_policy_loop(app: FastAPI) -> None:
    """
    수동 명령이 없을 때만 자동으로 IDLE/CHARGE를 enqueue.
    - PENDING/RUNNING 있으면 자동 enqueue 금지
    - 마지막 done_at 이후 6초 지나야 enqueue (체감 6초)
    - 배터리 < 30% => CHARGE, 아니면 IDLE_START
    """
    logger = get_logger()

    # 로봇별로 AUTO를 너무 자주 넣지 않도록 메모리 캐시(서버 재시작하면 리셋됨)
    last_auto_at: Dict[str, float] = {}

    logger.warning("AUTO POLICY: enabled (poll=%.1fs, trigger=%.1fs)", AUTO_POLL_SEC, AUTO_TRIGGER_SEC)

    while True:
        try:
            robots = db_query_all("SELECT robot_id, battery_pct FROM pinky_state_current")
            now = _dt_now()
            now_ts = _secs(now)

            for r in robots:
                robot_id = str(r.get("robot_id") or "").strip()
                if not robot_id:
                    continue

                # 1) 실행중/대기중 명령이 있으면 자동은 절대 넣지 않음
                row_cnt = db_query_one(
                    """
                    SELECT COUNT(*) AS c
                    FROM pinky_command_queue
                    WHERE robot_id=%s AND status IN ('PENDING','RUNNING')
                    """,
                    (robot_id,),
                )
                if row_cnt and int(row_cnt.get("c") or 0) > 0:
                    continue

                # 2) 마지막 완료(done_at) 기준으로 6초 이후에만 자동 enqueue
                row_last = db_query_one(
                    """
                    SELECT done_at
                    FROM pinky_command_queue
                    WHERE robot_id=%s AND done_at IS NOT NULL
                    ORDER BY done_at DESC
                    LIMIT 1
                    """,
                    (robot_id,),
                )

                if row_last and isinstance(row_last.get("done_at"), datetime):
                    done_at: datetime = row_last["done_at"]
                    elapsed = (now - done_at).total_seconds()
                    if elapsed < AUTO_TRIGGER_SEC:
                        continue
                else:
                    # 히스토리가 없으면 "기본 start"를 1회 넣어줌(스팸 방지)
                    if last_auto_at.get(robot_id) is not None:
                        continue

                # 3) 너무 자주 넣지 않기(안전장치)
                la = last_auto_at.get(robot_id, 0.0)
                if (now_ts - la) < AUTO_COOLDOWN_SEC:
                    continue

                # 4) 배터리 기준 판단
                bp = r.get("battery_pct")
                command = "IDLE_START"
                detail = "배터리 정보 없음 → IDLE_START"

                if bp is not None:
                    try:
                        bpv = float(bp)
                        if bpv < AUTO_BATTERY_THRESHOLD:
                            command = "CHARGE"
                            detail = f"배터리 {bpv:.0f}% < {AUTO_BATTERY_THRESHOLD:.0f}% → CHARGE"
                        else:
                            command = "IDLE_START"
                            detail = f"배터리 {bpv:.0f}% ≥ {AUTO_BATTERY_THRESHOLD:.0f}% → IDLE_START"
                    except Exception:
                        command = "IDLE_START"
                        detail = "배터리 파싱 실패 → IDLE_START"

                _enqueue_auto(robot_id, command, args=None, detail=detail)
                last_auto_at[robot_id] = now_ts

        except Exception as e:
            logger.warning("AUTO POLICY loop error: %s", repr(e))

        await asyncio.sleep(AUTO_POLL_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = get_logger()
    logger.warning("========== TASHO FastAPI boot ==========")

    # 1) DB schema 준비
    try:
        ensure_schema()
        log_event("SERVER", "INFO", "SCHEMA_OK", "DB schema ensured")
    except Exception as e:
        log_event("SERVER", "ERROR", "SCHEMA_FAIL", repr(e))

    # 2) 서버 켜짐 로그
    ip = _best_effort_host_ip()
    logger.warning("SERVER STARTED (visible log).")
    logger.warning("Health check: http://%s:8000/health  (or /api/health)", ip)
    log_event("SERVER", "INFO", "SERVER_START", "FastAPI server started")

    # 3) AUTO POLICY 태스크 시작
    auto_task = asyncio.create_task(_auto_policy_loop(app))

    try:
        yield
    finally:
        auto_task.cancel()
        try:
            await auto_task
        except Exception:
            pass

        log_event("SERVER", "INFO", "SERVER_STOP", "FastAPI server stopped")
        logger.warning("========== TASHO FastAPI stopped ==========")


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(pinky_router)
    app.include_router(events_router)
    if arm_router is not None:
        app.include_router(arm_router)

    return app
