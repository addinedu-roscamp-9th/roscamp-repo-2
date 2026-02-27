# server_core/schema.py
from __future__ import annotations

from .db import db_execute, db_query_one


def _ensure_column(table: str, col: str, ddl: str) -> None:
    row = db_query_one(
        """
        SELECT COUNT(*) AS c
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table, col),
    )
    if row and int(row.get("c", 0)) == 0:
        db_execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_schema() -> None:
    # -----------------------------
    # pinky_command_queue
    # -----------------------------
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS pinky_command_queue (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          robot_id VARCHAR(64) NOT NULL,
          command VARCHAR(64) NOT NULL,
          args_json TEXT,
          status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
          src VARCHAR(16) NOT NULL DEFAULT 'GUI',
          is_auto TINYINT NOT NULL DEFAULT 0,
          detail VARCHAR(255) NOT NULL DEFAULT '',
          created_at DATETIME(6) NOT NULL,
          claimed_at DATETIME(6) NULL,
          done_at DATETIME(6) NULL,
          INDEX idx_robot_status (robot_id, status),
          INDEX idx_robot_created (robot_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    # ✅ ack 결과를 넣는 컬럼: 없으면 추가 (기존 코드와 호환)
    _ensure_column(
        "pinky_command_queue",
        "result_detail",
        "result_detail VARCHAR(255) NOT NULL DEFAULT ''",
    )

    # -----------------------------
    # pinky_state_current
    # -----------------------------
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS pinky_state_current (
          robot_id VARCHAR(64) PRIMARY KEY,
          fsm_state VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
          task_state VARCHAR(32) NOT NULL DEFAULT 'NONE',
          goal_x DOUBLE NULL,
          goal_y DOUBLE NULL,
          goal_yaw DOUBLE NULL,
          pose_x DOUBLE NULL,
          pose_y DOUBLE NULL,
          pose_yaw DOUBLE NULL,
          battery_pct DOUBLE NULL,
          dock_state VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
          docking_state VARCHAR(32) NOT NULL DEFAULT 'NONE',
          updated_at DATETIME(6) NOT NULL,
          last_update DATETIME(6) NOT NULL,
          cooldown_until DATETIME(6) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    # -----------------------------
    # arm_command_queue
    # -----------------------------
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS arm_command_queue (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          client_id VARCHAR(32) NOT NULL,
          command VARCHAR(64) NOT NULL,
          status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
          detail VARCHAR(255) NOT NULL DEFAULT '',
          src VARCHAR(32) NOT NULL DEFAULT 'SERVER',
          created_at DATETIME(6) NOT NULL,
          claimed_at DATETIME(6) NULL,
          done_at DATETIME(6) NULL,
          INDEX idx_client_status (client_id, status),
          INDEX idx_client_created (client_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    # -----------------------------
    # arm_state_current
    # -----------------------------
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS arm_state_current (
          client_id VARCHAR(32) PRIMARY KEY,
          state VARCHAR(16) NOT NULL DEFAULT 'READY',
          job VARCHAR(64) NOT NULL DEFAULT 'NONE',
          detected TINYINT(1) NOT NULL DEFAULT 0,
          warn VARCHAR(255) NOT NULL DEFAULT '--',
          updated_at DATETIME(6) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    # -----------------------------
    # event_log
    # -----------------------------
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS event_log (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          created_at DATETIME(6) NOT NULL,
          src VARCHAR(32) NOT NULL,
          level VARCHAR(16) NOT NULL,
          event VARCHAR(64) NOT NULL,
          detail TEXT NOT NULL,
          robot_id VARCHAR(64) NULL,
          INDEX idx_created_at (created_at),
          INDEX idx_src_created (src, created_at),
          INDEX idx_robot_id (robot_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    # -----------------------------
    # payment_log
    # -----------------------------
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS payment_log (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          approval_code VARCHAR(32) NOT NULL,
          robot_id VARCHAR(64) NOT NULL,
          amount INT NULL,
          status VARCHAR(16) NOT NULL DEFAULT 'APPROVED',
          src VARCHAR(32) NOT NULL DEFAULT 'USER_UI',
          note VARCHAR(255) NOT NULL DEFAULT '',
          created_at DATETIME(6) NOT NULL,
          INDEX idx_payment_created (created_at),
          INDEX idx_payment_robot_created (robot_id, created_at),
          INDEX idx_payment_code (approval_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
