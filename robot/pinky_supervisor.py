from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

import requests

# requests 재사용(연결 재활용) + 타임아웃 통일
_SESSION = requests.Session()


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


# -----------------------------
# Single-instance lock (per robot_id)
# -----------------------------
class SingleInstance:
    """
    같은 robot_id로 supervisor를 2번 켜는 걸 원천 차단합니다.
    (systemd + 수동 실행 / tmux 중복 / 재실행 꼬임 방지)
    """

    def __init__(self, robot_id: str):
        self.robot_id = robot_id
        self.path = f"/tmp/pinky_supervisor_{robot_id}.lock"
        self._fp = None

    def acquire_or_exit(self) -> None:
        try:
            import fcntl
        except Exception as e:
            log(f"[락] fcntl 사용 불가(환경 문제): {e} (확실하지 않음)")
            return

        self._fp = open(self.path, "w")
        try:
            fcntl.flock(self._fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            log(
                f"[락] 이미 실행 중인 슈퍼바이저가 있습니다. 종료합니다. (robot_id={self.robot_id})"
            )
            sys.exit(2)

        try:
            self._fp.write(str(os.getpid()))
            self._fp.flush()
        except Exception:
            pass

    def release(self) -> None:
        try:
            import fcntl

            if self._fp:
                fcntl.flock(self._fp, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            if self._fp:
                self._fp.close()
        except Exception:
            pass


@dataclass
class BatteryConfig:
    topic: str = "/battery_percent"
    hz: float = 1.0
    server_post: int = 1
    publish_pct: int = 0


class BatteryWorker:
    def __init__(
        self,
        robot_id: str,
        server_base: str,
        cfg: BatteryConfig,
        state_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ):
        self.robot_id = robot_id
        self.server_base = server_base.rstrip("/")
        self.cfg = cfg
        self._state_provider = state_provider
        self._lock = threading.Lock()
        self._pct: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def get_pct(self) -> Optional[float]:
        with self._lock:
            return self._pct

    def _set_pct(self, v: Optional[float]) -> None:
        with self._lock:
            self._pct = v

    def _run(self) -> None:
        try:
            import rclpy
            from rclpy.node import Node
            from std_msgs.msg import Int32
        except Exception as e:
            log(f"[배터리 워커] rclpy import 실패: {e}")
            return

        class _Node(Node):
            def __init__(self, outer: "BatteryWorker"):
                super().__init__(f"battery_worker_{outer.robot_id}")
                self.outer = outer

                self.sub = self.create_subscription(
                    Int32, outer.cfg.topic, self.on_msg, 10
                )

            def on_msg(self, msg: Int32) -> None:
                try:
                    v = float(msg.data)
                except Exception:
                    v = None
                self.outer._set_pct(v)

        rclpy.init(args=None)
        node = _Node(self)

        last_post = 0.0
        period = 1.0 / max(float(self.cfg.hz), 0.2)

        try:
            while rclpy.ok() and (not self._stop.is_set()):
                rclpy.spin_once(node, timeout_sec=0.2)

                now = time.time()
                if now - last_post >= period:
                    last_post = now
                    if int(self.cfg.server_post or 0) == 1:
                        st = self._state_provider() if self._state_provider else {}
                        _post_state(
                            self.server_base,
                            self.robot_id,
                            fsm_state=str(st.get("fsm_state") or "IDLE"),
                            task_state=str(st.get("task_state") or "NONE"),
                            battery_pct=self.get_pct(),
                        )
        finally:
            try:
                node.destroy_node()
            except Exception:
                pass
            try:
                rclpy.shutdown()
            except Exception:
                pass


def http_post_json(
    url: str, payload: Dict[str, Any], timeout: float = 2.0, retries: int = 1
) -> Optional[Dict[str, Any]]:
    last: Optional[Exception] = None
    for _ in range(max(int(retries), 0) + 1):
        try:
            r = _SESSION.post(url, json=payload, timeout=timeout)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception as e:
            last = e
            time.sleep(0.15)
    return None


def http_get_json(
    url: str, timeout: float = 2.0, retries: int = 1
) -> Optional[Dict[str, Any]]:
    last: Optional[Exception] = None
    for _ in range(max(int(retries), 0) + 1):
        try:
            r = _SESSION.get(url, timeout=timeout)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception as e:
            last = e
            time.sleep(0.15)
    return None


def _post_state(
    server_base: str,
    robot_id: str,
    fsm_state: str,
    task_state: str,
    battery_pct: Optional[float],
) -> None:
    url = f"{server_base}/api/pinky/state"
    body = {
        "robot_id": robot_id,
        "fsm_state": fsm_state,
        "task_state": task_state,
        "battery_pct": (float(battery_pct) if battery_pct is not None else None),
        "dock_state": "UNKNOWN",
        "docking_state": "NONE",
        "cooldown_until": None,
    }
    http_post_json(url, body, timeout=2.0, retries=2)


def run_node(cmd: str, mode_name: str, node_name: str) -> int:
    log(f"[모드] {mode_name} | {node_name} 시작")
    log(f"[노드] {node_name} 실행 시작 | {cmd}")
    try:
        p = subprocess.Popen(cmd, shell=True)
        code = p.wait()
        if code == 0:
            log(f"[노드] {node_name} 실행 종료 | 성공 | exit={code}")
        else:
            log(f"[노드] {node_name} 실행 종료 | 실패 | exit={code}")
        return int(code)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        log(f"[노드] {node_name} 실행 예외: {e}")
        return 999


def queue_command(
    server_base: str,
    robot_id: str,
    command: str,
    src: str,
    detail: str = "",
    args: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    url = f"{server_base}/api/pinky/queue_command"
    payload = {
        "robot_id": robot_id,
        "command": command,
        "src": src,
        "detail": detail,
        "args": args or {},
    }
    resp = http_post_json(url, payload, timeout=2.0, retries=2)
    if not resp or not resp.get("ok"):
        return None
    try:
        return int(resp.get("cmd_id") or 0) or None
    except Exception:
        return None


def next_command(
    server_base: str, robot_id: str, only_manual: int = 0
) -> Tuple[Optional[str], Optional[int], Dict[str, Any], Dict[str, Any]]:
    url = f"{server_base}/api/pinky/next_command?robot_id={robot_id}&only_manual={int(only_manual or 0)}"
    resp = http_get_json(url, timeout=2.0, retries=2)
    if not resp:
        return None, None, {}, {}
    cmd = resp.get("cmd", None)
    if cmd is None:
        return None, None, {}, {}
    try:
        cmd_id = int(resp.get("cmd_id")) if resp.get("cmd_id") is not None else None
    except Exception:
        cmd_id = None
    args = resp.get("args") if isinstance(resp.get("args"), dict) else {}
    meta = {
        "src": resp.get("src"),
        "is_auto": (
            int(resp.get("is_auto") or 0) if resp.get("is_auto") is not None else 0
        ),
    }
    return str(cmd).upper(), cmd_id, args, meta


def ack_command(
    server_base: str,
    robot_id: str,
    cmd_id: int,
    status: str,
    detail: str = "",
    result_detail: str = "",
) -> None:
    url = f"{server_base}/api/pinky/ack_command"
    payload = {
        "robot_id": robot_id,
        "cmd_id": int(cmd_id),
        "status": status,
        "detail": detail,
        "result_detail": result_detail,
    }
    http_post_json(url, payload, timeout=2.0, retries=2)


@dataclass
class Paths:
    idle_start: str = "/home/pinky/pinky_agent/pinky_idle_start.py"
    docking: str = "/home/pinky/pinky_agent/pinky_click_nav2_pid_docking.py"
    charge: str = "/home/pinky/pinky_agent/pinky_charge.py"
    after_charge: str = "/home/pinky/pinky_agent/pinky_after_charge.py"
    idle_origin: str = "/home/pinky/pinky_agent/pinky_idle_origin.py"


@dataclass
class Config:
    server_base: str
    robot_id: str
    post_dock_wait_sec: float = 5.0
    post_dock_poll_hz: float = 2.0
    next_cmd_poll_hz: float = 2.0


class Supervisor:
    def __init__(self, cfg: Config, paths: Paths, batt_cfg: BatteryConfig):
        self.cfg = cfg
        self.paths = paths
        self._stop = False
        self._state_lock = threading.Lock()
        self._fsm_state = "IDLE"
        self._task_state = "NONE"
        self._batt = BatteryWorker(
            cfg.robot_id,
            cfg.server_base,
            batt_cfg,
            state_provider=self._state_snapshot,
        )
        self._current_docking_cmd_id: Optional[int] = None
        self._next_idle_node: str = "START"
        self._manual_override_in_cycle: bool = False

    def _set_mode(self, fsm_state: str, task_state: str) -> None:
        changed = False
        with self._state_lock:
            new_fsm = str(fsm_state or "IDLE")
            new_task = str(task_state or "NONE")
            changed = (new_fsm != self._fsm_state) or (new_task != self._task_state)
            self._fsm_state = new_fsm
            self._task_state = new_task

        if changed:
            self._push_state_now()

    def _push_state_now(self) -> None:
        _post_state(
            self.cfg.server_base,
            self.cfg.robot_id,
            fsm_state=self._fsm_state,
            task_state=self._task_state,
            battery_pct=self._batt.get_pct(),
        )

    def _state_snapshot(self) -> Dict[str, Any]:
        with self._state_lock:
            return {"fsm_state": self._fsm_state, "task_state": self._task_state}

    def start(self) -> None:
        self._batt.start()
        log(
            f"[슈퍼바이저] 시작 | robot_id={self.cfg.robot_id} | server={self.cfg.server_base}"
        )

        while not self._stop:
            if self._next_idle_node == "ORIGIN":
                self._set_mode("RUNNING", "IDLE_ORIGIN")
                run_node(
                    f"python3 {self.paths.idle_origin}",
                    "대기 모드",
                    "아이들 오리진 노드",
                )
                self._set_mode("IDLE", "NONE")
                self._next_idle_node = "START"
                log(
                    "[시퀀스] IDLE_ORIGIN 종료 -> 사이클 종료, 다음 사이클은 아이들 스타트부터"
                )
                continue
            else:
                self._set_mode("RUNNING", "IDLE_START")
                run_node(
                    f"python3 {self.paths.idle_start}",
                    "대기 모드",
                    "아이들 스타트 노드",
                )
            self._run_docking_cycle()

    def stop(self) -> None:
        self._stop = True
        self._set_mode("IDLE", "STOPPED")
        self._batt.stop()

    def _run_docking_cycle(self) -> None:
        self._set_mode("RUNNING", "DOCKING")

        self._current_docking_cmd_id = None
        # DOCKING은 서버 자동시퀀스의 출발점이라 cmd_id 확보가 중요합니다.
        # 네트워크/서버 일시 문제 대비로 3회 재시도합니다.
        cmd_id: Optional[int] = None
        for attempt in range(1, 4):
            cmd_id = queue_command(
                self.cfg.server_base,
                self.cfg.robot_id,
                "DOCKING",
                src="SUPERVISOR",
                detail="via=ros_click_goal",
                args={"via": "ros_click_goal"},
            )
            if cmd_id is not None:
                break
            log(
                f"[도킹 트리거] DOCKING enqueue 실패 -> 재시도 {attempt}/3 (확실하지 않음)"
            )
            time.sleep(0.2)

        if cmd_id is None:
            log("[도킹 트리거] DOCKING enqueue 최종 실패(확실하지 않음)")
        else:
            self._current_docking_cmd_id = cmd_id
            log(
                f"[도킹 트리거] DOCKING enqueue 완료 | cmd_id={cmd_id} (서버 DB 로그/트리거)"
            )

        docking_exit = run_node(
            f"python3 {self.paths.docking}", "운행 모드", "도킹 노드(주행)"
        )

        manual_taken = self._absolute_wait_and_process_manual()
        self._manual_override_in_cycle = bool(manual_taken)

        if self._current_docking_cmd_id is None:
            log(
                "[도킹] DOCKING cmd_id 없음 -> 서버 자동시퀀스가 멈출 수 있음(확실하지 않음)"
            )
        else:
            if manual_taken:
                ack_command(
                    self.cfg.server_base,
                    self.cfg.robot_id,
                    self._current_docking_cmd_id,
                    "IGNORED",
                    detail=f"docking_exit={docking_exit} (manual_override)",
                )
                log("[도킹 ACK] IGNORED 전송(수동 명령 우선, 서버 자동분기 차단)")
            else:
                ack_command(
                    self.cfg.server_base,
                    self.cfg.robot_id,
                    self._current_docking_cmd_id,
                    "DONE",
                    detail=f"docking_exit={docking_exit}",
                )
                log("[도킹 ACK] DONE 전송(서버 자동 배터리 분기 시작)")

        self._drain_commands_until_loop_point()

    def _absolute_wait_and_process_manual(self) -> bool:
        sec = float(self.cfg.post_dock_wait_sec)
        self._set_mode("IDLE", "WAIT_MANUAL")
        log(f"[절대 대기] 도킹 종료 후 {sec:.1f}s 동안 수동 명령 우선 처리")
        log("[모드] 대기 모드 | 도킹 종료 후 수동 명령 대기(절대 대기)")
        log(f"[절대 대기] {sec:.1f}s 동안 수동 명령큐 폴링/처리")

        end_t = time.time() + sec
        interval = 1.0 / max(self.cfg.post_dock_poll_hz, 0.2)
        manual_taken = False

        while time.time() < end_t:
            cmd, cmd_id, args, meta = next_command(
                self.cfg.server_base, self.cfg.robot_id, only_manual=1
            )
            if cmd is None:
                time.sleep(interval)
                continue

            # 방어: DOCKING은 수동 처리 대상 아님
            if cmd == "DOCKING":
                log(
                    f"[절대 대기] (방어) DOCKING은 수동 처리 대상 아님 -> 스킵 (cmd_id={cmd_id})"
                )
                time.sleep(0.05)
                continue

            manual_taken = True
            log(f"[절대 대기] 수동 명령 처리: {cmd} (cmd_id={cmd_id})")
            self._execute_one_command(cmd, cmd_id, args, context="절대 대기")
            time.sleep(0.05)

        if not manual_taken:
            log("[절대 대기] 수동 명령 없음 -> 서버 자동 배터리 분기 정상 진행")
        return manual_taken

    def _execute_one_command(
        self, cmd: str, cmd_id: Optional[int], args: Dict[str, Any], context: str
    ) -> None:
        cmd_u = (cmd or "").upper().strip()
        if cmd_id is None:
            log(f"[{context}] cmd_id 없음: cmd={cmd_u} (확실하지 않음)")
            return

        if cmd_u in ("CAM_START", "CAM_STOP", "CAM_ON", "CAM_OFF"):
            ack_command(
                self.cfg.server_base,
                self.cfg.robot_id,
                cmd_id,
                "DONE",
                detail=f"{cmd_u} ack by supervisor",
            )
            log(f"[{context}] 카메라 명령 처리: {cmd_u} (ACK DONE)")
            return

        if cmd_u == "CHARGE":
            log(f"[{context}] 명령 처리: CHARGE")
            self._set_mode("CHARGE", "CHARGE")
            run_node(f"python3 {self.paths.charge}", "충전 모드", "차지 노드")
            ack_command(
                self.cfg.server_base, self.cfg.robot_id, cmd_id, "DONE", detail="exit=0"
            )
            self._set_mode("CHARGE", "WAIT_AFTER_CHARGE")
            return

        if cmd_u == "AFTER_CHARGE":
            log(f"[{context}] 명령 처리: AFTER_CHARGE")
            self._set_mode("IDLE", "AFTER_CHARGE")
            run_node(
                f"python3 {self.paths.after_charge}", "충전 모드", "에프터 차지 노드"
            )
            ack_command(
                self.cfg.server_base, self.cfg.robot_id, cmd_id, "DONE", detail="exit=0"
            )
            self._set_mode("IDLE", "NONE")
            return

        if cmd_u == "IDLE_ORIGIN":
            log(f"[{context}] 명령 처리: IDLE_ORIGIN")
            self._set_mode("RUNNING", "IDLE_ORIGIN")
            run_node(
                f"python3 {self.paths.idle_origin}", "대기 모드", "아이들 오리진 노드"
            )
            ack_command(
                self.cfg.server_base, self.cfg.robot_id, cmd_id, "DONE", detail="exit=0"
            )
            self._set_mode("IDLE", "NONE")
            return

        if cmd_u == "DOCKING":
            ack_command(
                self.cfg.server_base,
                self.cfg.robot_id,
                cmd_id,
                "FAILED",
                detail="unknown_cmd=DOCKING",
            )
            log(f"[{context}] DOCKING이 큐로 들어옴(예상 밖) -> FAILED 처리")
            return

        ack_command(
            self.cfg.server_base,
            self.cfg.robot_id,
            cmd_id,
            "FAILED",
            detail=f"unknown_cmd={cmd_u}",
        )
        log(f"[{context}] 알 수 없는 명령 -> FAILED: {cmd_u}")

    def _drain_commands_until_loop_point(self) -> None:
        poll_interval = 1.0 / max(self.cfg.next_cmd_poll_hz, 0.2)

        waited_for_after_charge = False
        after_charge_wait_deadline = 120.0
        t0 = time.time()

        while True:
            cmd, cmd_id, args, meta = next_command(
                self.cfg.server_base, self.cfg.robot_id, only_manual=0
            )
            if cmd is None:
                if (
                    waited_for_after_charge
                    and (time.time() - t0) < after_charge_wait_deadline
                ):
                    time.sleep(poll_interval)
                    continue
                if (not waited_for_after_charge) and (
                    not self._manual_override_in_cycle
                ):
                    self._next_idle_node = "ORIGIN"
                self._set_mode("IDLE", "NONE")
                self._manual_override_in_cycle = False
                return

            self._execute_one_command(cmd, cmd_id, args, context="후속 처리")

            if cmd in ("IDLE_ORIGIN", "AFTER_CHARGE"):
                log("[시퀀스] 종료 지점 도달 -> 아이들 스타트부터 시퀀스 반복")
                return

            if cmd == "CHARGE":
                waited_for_after_charge = True
                t0 = time.time()
                time.sleep(0.2)
                continue

            time.sleep(0.05)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--robot-id", required=True)
    ap.add_argument("--server-base", required=True)

    ap.add_argument("--battery-topic", default="/battery_percent")
    ap.add_argument("--battery-hz", type=float, default=1.0)
    ap.add_argument("--battery-server-post", type=int, default=1)

    ap.add_argument("--post-dock-wait", type=float, default=5.0)
    args = ap.parse_args()

    # ✅ single-instance lock
    lock = SingleInstance(args.robot_id)
    lock.acquire_or_exit()

    cfg = Config(
        server_base=args.server_base.rstrip("/"),
        robot_id=args.robot_id,
        post_dock_wait_sec=float(args.post_dock_wait),
    )
    paths = Paths()

    batt_cfg = BatteryConfig(
        topic=str(args.battery_topic),
        hz=float(args.battery_hz),
        server_post=int(args.battery_server_post),
    )

    sup = Supervisor(cfg, paths, batt_cfg)

    def _sig(_signo, _frame):
        try:
            sup.stop()
        finally:
            lock.release()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    log(
        f"배터리 워커 시작 | topic={batt_cfg.topic} | hz={batt_cfg.hz:.2f} | server_post={batt_cfg.server_post} | publish_pct={batt_cfg.publish_pct}"
    )
    try:
        sup.start()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
