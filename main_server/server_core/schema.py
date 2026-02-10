from __future__ import annotations

from typing import Any, Dict, Optional

from .db import db_execute, db_query_one


def _col_exists(table: str, col: str, db_name: str = "Tasho_server") -> bool:
    row = db_query_one(
        """
        SELECT 1 AS ok
        FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s AND column_name=%s
        LIMIT 1
        """,
        (db_name, table, col),
    )
    return bool(row)


def _ensure_column(table: str, col: str, ddl: str) -> None:
    if _col_exists(table, col):
        return
    db_execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_schema() -> None:
    # ---- Pinky state ----
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS pinky_state_current (
          robot_id       VARCHAR(64) NOT NULL PRIMARY KEY,
          fsm_state      VARCHAR(32) NOT NULL DEFAULT 'IDLE',
          task_state     VARCHAR(32) NOT NULL DEFAULT 'NONE',

          goal_x         DOUBLE NULL,
          goal_y         DOUBLE NULL,
          goal_yaw       DOUBLE NULL,

          pose_x         DOUBLE NULL,
          pose_y         DOUBLE NULL,
          pose_yaw       DOUBLE NULL,

          battery_pct    DOUBLE NULL,

          dock_state     VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
          docking_state  VARCHAR(32) NOT NULL DEFAULT 'NONE',

          updated_at     DATETIME(6) NOT NULL,
          last_update    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

          cooldown_until DATETIME(6) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    # 기존 운영 DB에서 cooldown_until이 없을 수도 있으니 컬럼만 보정 (DATETIME(6)로)
    _ensure_column("pinky_state_current", "cooldown_until", "cooldown_until DATETIME(6) NULL")

    # ---- Pinky command queue ----
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS pinky_command_queue (
          id           BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          robot_id     VARCHAR(64) NOT NULL,
          command      VARCHAR(64) NOT NULL,

          payload      TEXT NOT NULL,
          status       VARCHAR(16) NOT NULL DEFAULT 'PENDING',
          detail       VARCHAR(255) NOT NULL DEFAULT '',
          src          VARCHAR(32) NOT NULL DEFAULT 'SERVER',

          created_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
          available_at DATETIME(6) NULL,
          claimed_at   DATETIME(6) NULL,
          done_at      DATETIME(6) NULL,

          is_auto      TINYINT NOT NULL DEFAULT 0,
          args_json    JSON NULL,

          KEY idx_pinky_cmd_robot_status_created (robot_id, status, created_at),
          KEY idx_pinky_cmd_robot_available (robot_id, status, available_at),
          KEY idx_pinky_cmd_robot_created (robot_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    # 기존 테이블이 이미 있고 컬럼 일부가 없을 수 있으니 보정
    _ensure_column("pinky_command_queue", "payload", "payload TEXT NOT NULL")
    _ensure_column("pinky_command_queue", "detail", "detail VARCHAR(255) NOT NULL DEFAULT ''")
    _ensure_column("pinky_command_queue", "src", "src VARCHAR(32) NOT NULL DEFAULT 'SERVER'")
    _ensure_column("pinky_command_queue", "available_at", "available_at DATETIME(6) NULL")
    _ensure_column("pinky_command_queue", "claimed_at", "claimed_at DATETIME(6) NULL")
    _ensure_column("pinky_command_queue", "done_at", "done_at DATETIME(6) NULL")
    _ensure_column("pinky_command_queue", "is_auto", "is_auto TINYINT NOT NULL DEFAULT 0")
    _ensure_column("pinky_command_queue", "args_json", "args_json JSON NULL")

    # ---- Events table (Admin Timeline) ----
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS event_log (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
          src VARCHAR(64) NOT NULL,
          level VARCHAR(16) NOT NULL,
          event VARCHAR(64) NOT NULL,
          detail TEXT NOT NULL,

          KEY idx_event_created (created_at),
          KEY idx_event_src_created (src, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
