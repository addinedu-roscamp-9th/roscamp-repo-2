from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Optional, Tuple

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import db_fetchone, db_execute
from .schema import ensure_schema
from .log import log_event, get_logger
from .routers.health import router as health_router
from .routers.pinky import router as pinky_router
from .routers.events import router as events_router

# arm router is optional; import only if present
try:
    from .routers.arm import router as arm_router  # type: ignore
except Exception as e:
    arm_router = None
    # app_factory import 단계에서는 logger가 없을 수 있으니 print로도 남김
    try:
        get_logger().warning("ARM router disabled: %r", e)
    except Exception:
        print(f"[WARN] ARM router disabled: {e!r}")


def _best_effort_host_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def _register_middlewares(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _include_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(events_router)
    app.include_router(pinky_router, prefix="/api/pinky")

    if arm_router is not None:
        app.include_router(arm_router)


def _boot_banner(app: FastAPI) -> None:
    ip = _best_effort_host_ip()
    port = 8000
    try:
        get_logger().warning("========== TASHO FastAPI boot ==========")
        get_logger().warning("Health check: http://%s:%d/health  (or /api/health)", ip, port)
        get_logger().info("SERVER | SERVER_START | FastAPI server started")
    except Exception:
        print("========== TASHO FastAPI boot ==========")
        print(f"Health check: http://{ip}:{port}/health  (or /api/health)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # schema ensure
    try:
        ensure_schema()
        log_event("SERVER", "INFO", "SCHEMA_OK", "DB schema ensured")
    except Exception as e:
        try:
            get_logger().exception("SCHEMA ensure failed: %r", e)
        except Exception:
            print(f"[ERROR] SCHEMA ensure failed: {e!r}")

    _boot_banner(app)
    yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    _register_middlewares(app)
    _include_routers(app)
    return app
