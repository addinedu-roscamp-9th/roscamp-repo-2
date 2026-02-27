#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional, Tuple

import requests


def fetch_next_arm_command(
    sess: requests.Session,
    server_base: str,
    client_id: str,
    timeout: float,
) -> Tuple[Optional[str], Optional[int], str]:
    url = f"{server_base}/api/arm/next_command"
    try:
        r = sess.get(url, params={"client_id": client_id}, timeout=timeout)
        if r.status_code != 200:
            return None, None, ""
        data = r.json()
        cmd = data.get("cmd", None)
        cmd_id = data.get("cmd_id", None)
        detail = data.get("detail", "") or ""
        return str(cmd) if cmd else None, int(cmd_id) if cmd_id else None, str(detail)
    except Exception:
        return None, None, ""


def ack_arm_command(
    sess: requests.Session,
    server_base: str,
    client_id: str,
    cmd_id: Optional[int],
    status: str,
    detail: str,
    timeout: float,
) -> None:
    if cmd_id is None:
        return

    payload = {
        "client_id": client_id,
        "cmd_id": int(cmd_id),
        "status": str(status).upper(),
        "detail": (detail or "")[:255],
    }

    for ep in ("/api/arm/ack", "/api/arm/ack_command"):
        try:
            r = sess.post(f"{server_base}{ep}", json=payload, timeout=timeout)
            if r.status_code == 200:
                return
        except Exception:
            continue


def report_state(
    sess: requests.Session,
    server_base: str,
    client_id: str,
    state: str,
    job: str,
    warn: str,
    timeout: float,
) -> None:
    payload = {
        "client_id": client_id,
        "state": state,
        "job": job,
        "warn": (warn or "--")[:255],
        "detected": False,
        "src": "ARM_SUPERVISOR",
    }
    try:
        sess.post(f"{server_base}/api/arm/report_state", json=payload, timeout=timeout)
    except Exception:
        pass


def _build_worker_cmd(args: argparse.Namespace) -> list[str]:
    return [
        args.python,
        args.worker_script,
        "--worker",
        "--vision-pc-ip",
        args.vision_pc_ip,
        "--detect-wait-sec",
        str(args.detect_wait_sec),
        "--charge-wait-sec",
        str(args.charge_wait_sec),
        "--worker-bind-ip",
        args.worker_bind_ip,
        "--worker-cmd-port",
        str(args.worker_cmd_port),
        "--supervisor-result-ip",
        args.supervisor_result_ip,
        "--supervisor-result-port",
        str(args.supervisor_result_port),
    ]


def _ensure_worker_running(
    proc: Optional[subprocess.Popen[Any]],
    args: argparse.Namespace,
) -> subprocess.Popen[Any]:
    if proc is not None and proc.poll() is None:
        return proc

    cmd = _build_worker_cmd(args)
    print(f"[SUP] worker start: {' '.join(cmd)}", flush=True)
    return subprocess.Popen(cmd, cwd=args.worker_cwd)


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    default_worker_script = str(base_dir / "final_arm_charge.py")

    ap = argparse.ArgumentParser(description="Arm supervisor with heartbeat + worker control")
    ap.add_argument("--server-base", default="http://192.168.1.8:8000")
    ap.add_argument("--client-id", default="jetcobot1")
    ap.add_argument("--http-timeout", type=float, default=2.0)
    ap.add_argument("--poll", type=float, default=0.5)
    ap.add_argument("--heartbeat-sec", type=float, default=1.0)

    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--worker-script", default=default_worker_script)
    ap.add_argument("--worker-cwd", default=str(base_dir))

    ap.add_argument("--vision-pc-ip", default="192.168.1.8")
    ap.add_argument("--detect-wait-sec", type=float, default=45.0)
    ap.add_argument("--charge-wait-sec", type=float, default=10.0)

    ap.add_argument("--worker-bind-ip", default="127.0.0.1")
    ap.add_argument("--worker-cmd-port", type=int, default=18990)
    ap.add_argument("--supervisor-result-ip", default="127.0.0.1")
    ap.add_argument("--supervisor-result-port", type=int, default=18991)

    args = ap.parse_args()

    if not os.path.exists(args.worker_script):
        local_candidate = os.path.join(args.worker_cwd, "final_arm_charge.py")
        if os.path.exists(local_candidate):
            args.worker_script = local_candidate
        else:
            raise SystemExit(
                f"worker script not found: {args.worker_script} (or {local_candidate})"
            )

    sess = requests.Session()

    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    recv_sock.bind((args.supervisor_result_ip, args.supervisor_result_port))
    recv_sock.settimeout(0.2)

    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    worker_proc: Optional[subprocess.Popen[Any]] = None
    busy_cmd_id: Optional[int] = None
    busy_robot_id: str = "UNKNOWN"
    last_warn = "--"
    last_hb = 0.0

    try:
        while True:
            worker_proc = _ensure_worker_running(worker_proc, args)

            now = time.time()
            if now - last_hb >= max(0.2, args.heartbeat_sec):
                if busy_cmd_id is None:
                    report_state(
                        sess,
                        args.server_base,
                        args.client_id,
                        state="READY",
                        job="IDLE",
                        warn=last_warn,
                        timeout=args.http_timeout,
                    )
                else:
                    report_state(
                        sess,
                        args.server_base,
                        args.client_id,
                        state="BUSY",
                        job="START_CHARGE",
                        warn=last_warn,
                        timeout=args.http_timeout,
                    )
                last_hb = now

            # worker 결과 수신
            try:
                raw, _addr = recv_sock.recvfrom(8192)
                msg = json.loads(raw.decode(errors="ignore"))
                if str(msg.get("type", "")).upper() == "RESULT":
                    rid = int(msg.get("cmd_id")) if msg.get("cmd_id") is not None else None
                    status = str(msg.get("status", "FAILED")).upper()
                    detail = str(msg.get("detail", ""))
                    busy_robot_id = str(msg.get("robot_id", busy_robot_id))
                    ack_arm_command(
                        sess,
                        args.server_base,
                        args.client_id,
                        rid,
                        status,
                        detail,
                        args.http_timeout,
                    )
                    if status == "DONE":
                        last_warn = "--"
                    else:
                        last_warn = detail[:255] or "mission_failed"
                    busy_cmd_id = None
            except socket.timeout:
                pass
            except Exception as e:
                last_warn = f"worker_result_error={e}"[:255]

            # 바쁠 때는 새 명령 폴링하지 않음
            if busy_cmd_id is not None:
                time.sleep(max(0.05, args.poll / 2.0))
                continue

            cmd, cmd_id, detail = fetch_next_arm_command(
                sess,
                args.server_base,
                args.client_id,
                args.http_timeout,
            )
            if cmd is None:
                time.sleep(args.poll)
                continue

            if str(cmd).strip().upper() != "START_CHARGE":
                ack_arm_command(
                    sess,
                    args.server_base,
                    args.client_id,
                    cmd_id,
                    "FAILED",
                    f"unsupported_cmd={cmd}",
                    args.http_timeout,
                )
                time.sleep(0.05)
                continue

            payload = {
                "type": "RUN",
                "job": "START_CHARGE",
                "cmd_id": cmd_id,
                "detail": detail,
            }
            send_sock.sendto(
                json.dumps(payload, ensure_ascii=False).encode(),
                (args.worker_bind_ip, args.worker_cmd_port),
            )
            busy_cmd_id = cmd_id
            last_warn = "--"

    finally:
        if worker_proc is not None and worker_proc.poll() is None:
            try:
                worker_proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
