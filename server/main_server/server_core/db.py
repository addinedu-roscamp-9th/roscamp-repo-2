from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import pymysql

# ===== DB CONFIG =====
DB_HOST = "192.168.1.8"
DB_PORT = 3306
DB_USER = "pinky13_user"
DB_PASS = "1"
DB_NAME = "Tasho_server"

# 짧게 잡아서, DB 문제 때 API가 “멈춘 것처럼” 안 보이게 함
DB_CONNECT_TIMEOUT_SEC = 2
DB_RW_TIMEOUT_SEC = 3


def now_iso() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _get_conn():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
        connect_timeout=DB_CONNECT_TIMEOUT_SEC,
        read_timeout=DB_RW_TIMEOUT_SEC,
        write_timeout=DB_RW_TIMEOUT_SEC,
    )


def db_execute(sql: str, params: tuple = ()) -> None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    finally:
        conn.close()


def db_execute_return_id(sql: str, params: tuple = ()) -> int:
    """INSERT 실행 후 lastrowid(= auto increment PK)를 반환합니다.

    - autocommit=True라서 별도 commit 불필요
    - INSERT가 아니거나, lastrowid가 없으면 0 반환
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            try:
                return int(cur.lastrowid or 0)
            except Exception:
                return 0
    finally:
        conn.close()


def db_query_one(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def db_query_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()


# Backward compatible aliases (older code)
db_fetchone = db_query_one
db_fetchall = db_query_all


def _get_primary_key_column(table_name: str) -> Optional[str]:
    """
    arm.py에서 사용하는 유틸.
    - 테이블의 PK 컬럼명을 INFORMATION_SCHEMA에서 조회
    - 실패하거나 없으면 None 반환 (호출부에서 'id'로 fallback)
    """
    if not table_name:
        return None

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT kcu.COLUMN_NAME AS pk
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                 AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
                 AND tc.TABLE_NAME = kcu.TABLE_NAME
                WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                  AND tc.TABLE_SCHEMA = %s
                  AND tc.TABLE_NAME = %s
                ORDER BY kcu.ORDINAL_POSITION
                LIMIT 1
                """,
                (DB_NAME, table_name),
            )
            row = cur.fetchone()
            if row and row.get("pk"):
                return str(row["pk"])
    except Exception:
        return None
    finally:
        conn.close()

    return None