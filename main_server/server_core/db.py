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
