#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional


def ts() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


@dataclass
class ProcSpec:
    name: str
    cmd: List[str]
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    proc: Optional[subprocess.Popen] = None
    last_start: float = 0.0
    restart_count: int = 0


def _popen(spec: ProcSpec) -> subprocess.Popen:
    log(f"[START] {spec.name}: {' '.join(shlex.quote(x) for x in spec.cmd)}")
    p = subprocess.Popen(
        spec.cmd,
        cwd=spec.cwd,
        env=spec.env,
        stdout=sys.stdout,   # 부모 터미널로 그대로 출력
        stderr=sys.stderr,
        preexec_fn=os.setsid # 자식 프로세스 그룹 생성(종료 처리를 깔끔하게)
    )
    spec.proc = p
    spec.last_start = time.time()
    return p


def _terminate_process_group(p: subprocess.Popen, timeout: float = 3.0) -> None:
    if p.poll() is not None:
        return
    try:
        pgid = os.getpgid(p.pid)
        os.killpg(pgid, signal.SIGTERM)
    except Exception:
        try:
            p.terminate()
        except Exception:
            pass

    t0 = time.time()
    while time.time() - t0 < timeout:
        if p.poll() is not None:
            return
        time.sleep(0.05)

    # 강제 종료
    try:
        pgid = os.getpgid(p.pid)
        os.killpg(pgid, signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def build_specs(args: argparse.Namespace) -> List[ProcSpec]:
    # 스크립트 경로(마스터 환경에 맞춰 조절 가능)
    # 기본: 런처 파일과 같은 폴더에 final_pinky1.py/final_pinky2.py/arm_vision.py 가 있다고 가정
    base_dir = os.path.abspath(args.scripts_dir)
    pinky1_path = os.path.join(base_dir, args.pinky1_script)
    pinky2_path = os.path.join(base_dir, args.pinky2_script)
    arm_path = os.path.join(base_dir, args.arm_script)

    # 공통 python 실행기(가상환경 쓰면 --python 으로 바꿔치기)
    py = args.python

    specs: List[ProcSpec] = []

    env_p1 = dict(os.environ)
    env_p1["ROS_DOMAIN_ID"] = str(args.pinky1_domain)

    env_p2 = dict(os.environ)
    env_p2["ROS_DOMAIN_ID"] = str(args.pinky2_domain)

    # Pinky1: YOLO+REDSTOP (ROS node)
    specs.append(
        ProcSpec(
            name="pinky1_ai",
            cmd=[
                py, pinky1_path,
                "--in-port", str(args.pinky1_in),
                "--out-host", args.out_host,
                "--out-port", str(args.pinky1_out),
                "--payload", str(args.payload),
                "--fps", str(args.fps),
                "--bitrate", str(args.bitrate),
                "--labels-topic", "/yolo/detection_labels",
                "--stop-topic", "/safety/stop",
                "--node-name", "unified_yolo_red_stop_pinky1",
            ] + (["--no-preview"] if args.no_preview else []),
            cwd=base_dir,
            env=env_p1,
        )
    )

    # Pinky2: YOLO+REDSTOP (필터 강화판)
    specs.append(
        ProcSpec(
            name="pinky2_ai",
            cmd=[
                py, pinky2_path,
                "--in-port", str(args.pinky2_in),
                "--out-host", args.out_host,
                "--out-port", str(args.pinky2_out),
                "--payload", str(args.payload),
                "--fps", str(args.fps),
                "--bitrate", str(args.bitrate),
                "--labels-topic", "/yolo/detection_labels",
                "--stop-topic", "/safety/stop",
                "--node-name", "unified_yolo_red_stop_pinky2",
            ] + (["--no-preview"] if args.no_preview else []),
            cwd=base_dir,
            env=env_p2,
        )
    )

    # Arm vision: YOLO(charge) -> ArUco -> UDP to JetCobot + overlay 송출
    specs.append(
        ProcSpec(
            name="arm_vision",
            cmd=[
                py, arm_path,
                "--in-port", str(args.arm_in),
                "--out-host", args.out_host,
                "--out-port", str(args.arm_out),
                "--payload", str(args.payload),
                "--fps", str(args.fps),
                "--bitrate", str(args.bitrate),
                "--jetcobot-ip", args.jetcobot_ip,
                "--jetcobot-port", str(args.jetcobot_port),
                "--cmd-port", str(args.cmd_port),
            ] + (["--no-imshow"] if args.no_imshow else []),
            cwd=base_dir,
        )
    )

    return specs


def main() -> None:
    ap = argparse.ArgumentParser(description="One-click AI launcher (pinky1/pinky2/arm vision)")

    default_python = sys.executable
    local_venv_python = os.path.join(os.path.abspath("."), ".venv", "bin", "python")
    if os.path.exists(local_venv_python):
        default_python = local_venv_python

    # 경로/실행기
    ap.add_argument("--scripts-dir", default=".", help="final_pinky1.py 등이 있는 폴더")
    ap.add_argument("--python", default=default_python, help="python 실행기(가상환경이면 경로 지정)")

    ap.add_argument("--pinky1-script", default="final_pinky1.py")
    ap.add_argument("--pinky2-script", default="final_pinky2.py")
    ap.add_argument("--arm-script", default="arm_vision.py")

    # 공통 스트림 파라미터
    ap.add_argument("--out-host", default="192.168.1.8", help="관리자 GUI PC IP")
    ap.add_argument("--payload", type=int, default=96)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bitrate", type=int, default=1200)

    # 마스터가 지정한 기본 포트 매핑
    ap.add_argument("--pinky1-in", type=int, default=5000)
    ap.add_argument("--pinky1-out", type=int, default=6000)

    ap.add_argument("--pinky2-in", type=int, default=5001)
    ap.add_argument("--pinky2-out", type=int, default=6001)

    ap.add_argument("--pinky1-domain", type=int, default=9)
    ap.add_argument("--pinky2-domain", type=int, default=14)

    ap.add_argument("--arm-in", type=int, default=5003)
    ap.add_argument("--arm-out", type=int, default=5002)

    # arm_vision UDP
    ap.add_argument("--jetcobot-ip", default="192.168.1.21")
    ap.add_argument("--jetcobot-port", type=int, default=8888)
    ap.add_argument("--cmd-port", type=int, default=8889)

    # UI/Preview 옵션
    ap.add_argument("--no-preview", dest="no_preview", action="store_true", help="pinky 프리뷰 창 끄기")
    ap.add_argument("--preview", dest="no_preview", action="store_false", help="pinky 프리뷰 창 켜기")
    ap.add_argument("--no-imshow", dest="no_imshow", action="store_true", help="arm_vision Smart Vision 창 끄기")
    ap.add_argument("--imshow", dest="no_imshow", action="store_false", help="arm_vision Smart Vision 창 켜기")
    ap.set_defaults(no_preview=True, no_imshow=True)

    # 안정성 옵션
    ap.add_argument("--auto-restart", action="store_true", help="자식 프로세스 죽으면 자동 재시작")
    ap.add_argument("--restart-backoff", type=float, default=1.0, help="재시작 최소 대기(초)")

    args = ap.parse_args()

    specs = build_specs(args)

    stopping = False

    def _handle(sig, frame):  # noqa: ARG001
        nonlocal stopping
        if stopping:
            return
        stopping = True
        log(f"[SIGNAL] {sig} received -> stopping all...")
        for sp in specs:
            if sp.proc is not None:
                _terminate_process_group(sp.proc)
        log("[DONE] all stopped.")

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    # 최초 실행
    for sp in specs:
        _popen(sp)

    log("[RUN] launcher started. Ctrl+C to stop.")

    # 감시 루프
    try:
        while not stopping:
            for sp in specs:
                p = sp.proc
                if p is None:
                    continue

                rc = p.poll()
                if rc is None:
                    continue  # still running

                log(f"[EXIT] {sp.name} exited (code={rc})")

                if not args.auto_restart or stopping:
                    continue

                # 재시작 백오프
                elapsed = time.time() - sp.last_start
                wait_more = max(0.0, args.restart_backoff - elapsed)
                if wait_more > 0:
                    time.sleep(wait_more)

                sp.restart_count += 1
                log(f"[RESTART] {sp.name} (count={sp.restart_count})")
                _popen(sp)

            time.sleep(0.2)

    finally:
        # 혹시라도 남아있으면 정리
        for sp in specs:
            if sp.proc is not None:
                _terminate_process_group(sp.proc)
        log("[FINALLY] cleanup complete.")


if __name__ == "__main__":
    main()
