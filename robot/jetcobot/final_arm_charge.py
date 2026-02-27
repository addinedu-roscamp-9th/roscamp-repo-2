import argparse
import json
import math
import socket
import time
from typing import Optional, Tuple

import numpy as np
import requests
from pymycobot.mycobot280 import MyCobot280

# --- [공학적 상수 설정] ---
UDP_IP, UDP_PORT = "0.0.0.0", 8888
ABS_MAX_REACH = 275.0  # 로봇 팔의 물리적 최대 도달 거리 (mm)

GAIN_Y = -1.0
CAM_TO_GRIP_OFFSET = 50.0  # 카메라-그리퍼 물리적 오프셋
FINAL_TARGET_DIST = 10.0
EXTRA_Y_OFFSET = 4.5

SAG_COMPENSATION_RATIO = 0.02  # 전진 시 중력에 의한 처짐 보정 계수
TILT_COMPENSATION = 0.3        # 진입 시 그리퍼 각도 보정

# 주요 자세 정의
POS_HOME = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # 대기 및 휴식 자세
POS_STANDBY = [-135.25, -15.92, -130, -40.74, 133.15, -145.55]  # 감지 대기 자세
POS_RETRACT = [-135.25, -15.92, -130, -40.74, 133.15, -145.55]  # 충전 후 후퇴 자세

# --- [유틸리티 함수] ---


def wait_until_stop(mc: MyCobot280):
    time.sleep(0.1)
    while mc.is_moving():
        time.sleep(0.1)


def flush_udp_buffer(sock: socket.socket):
    sock.setblocking(False)
    try:
        while True:
            sock.recvfrom(4096)
    except Exception:
        pass
    sock.setblocking(True)
    print("🧹 [System] UDP 소켓 버퍼 초기화 완료")


def get_average_distance(sock: socket.socket, duration: float = 5.0) -> Optional[float]:
    start_time = time.time()
    buffer_dz = []
    print(f"📏 [측정] {duration:.1f}초간 AR 데이터를 수집하여 평균 거리를 산출합니다...")
    flush_udp_buffer(sock)

    while (time.time() - start_time) < duration:
        try:
            sock.settimeout(0.2)
            data, _ = sock.recvfrom(2048)
            msg = data.decode(errors="ignore").strip()
            if msg.startswith("AR,"):
                parts = msg.split(",")
                if len(parts) >= 4:
                    dz = float(parts[3])
                    if 10.0 < dz < 1000.0:
                        buffer_dz.append(dz)
        except socket.timeout:
            continue
        except Exception:
            continue

    if not buffer_dz:
        return None
    return float(np.mean(buffer_dz))


# --- [서버 통신 레이어] ---


def fetch_next_arm_command(
    sess: requests.Session, server_base: str, client_id: str, timeout: float
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
):
    """서버 호환 ACK 전송.
    - 기본: /api/arm/ack
    - 호환: /api/arm/ack_command
    """
    if cmd_id is None:
        return

    payload = {
        "client_id": client_id,
        "cmd_id": cmd_id,
        "status": status,
        "detail": (detail or "")[:255],
    }

    endpoints = ["/api/arm/ack", "/api/arm/ack_command"]
    ok = False
    for ep in endpoints:
        try:
            r = sess.post(f"{server_base}{ep}", json=payload, timeout=timeout)
            if r.status_code == 200:
                ok = True
                break
        except Exception:
            continue

    if not ok:
        print("⚠️ [Network] ACK 전송 실패(확실하지 않음)")


def notify_charge_done(sess: requests.Session, server_base: str, robot_id: str, timeout: float) -> bool:
    """(레거시) 충전 완료를 핑키 쪽으로 직접 notify.
    서버에 해당 엔드포인트가 없을 수 있으므로, 실패해도 임무는 계속 진행합니다.
    """
    payload = {"robot_id": robot_id, "src": "ARM", "detail": "charge done"}
    for endpoint in ["/api/pinky/charge_complete", "/api/pinky/charge_done"]:
        try:
            r = sess.post(f"{server_base}{endpoint}", json=payload, timeout=timeout)
            if r.status_code == 200:
                return True
        except Exception:
            continue
    print("ℹ️ [Compat] /api/pinky/charge_done 계열 미지원으로 notify 실패(정상일 수 있음)")
    return False


def send_reset_to_vision(vision_pc_ip: str, vision_cmd_port: int = 8889):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"RESET", (vision_pc_ip, vision_cmd_port))
        s.close()
        print(f"📨 [Vision] 리셋 신호 전송 완료 ({vision_pc_ip})")
    except Exception as e:
        print(f"⚠️ [Vision] 리셋 신호 전송 실패: {e}")


def wait_charge_detected(sock: socket.socket, max_wait_sec: float = 30.0):
    print(f"📡 [Detection] 'CHARGE_DETECTED' 신호 대기 중... (최대 {max_wait_sec}초)")
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        try:
            sock.settimeout(2.0)
            data, _ = sock.recvfrom(1024)
            if b"CHARGE_DETECTED" in data:
                print("🎯 [Success] 충전구 확인! 도킹 시퀀스를 시작합니다.")
                return
        except socket.timeout:
            continue
    raise TimeoutError("충전구 탐지 타임아웃")


def parse_robot_id_from_detail(detail: str) -> Optional[str]:
    if not detail:
        return None
    key = "robot_id="
    idx = detail.find(key)
    if idx < 0:
        return None
    res = detail[idx + len(key) :].split()[0].strip("|,;")
    return res


def run_start_charge_mission(
    mc: MyCobot280,
    sock: socket.socket,
    args: argparse.Namespace,
    robot_id: str,
) -> Tuple[str, str]:
    try:
        # 초기화 및 비전 리셋
        flush_udp_buffer(sock)
        send_reset_to_vision(args.vision_pc_ip)

        # 단계 1: 감지 대기 자세로 이동
        print("📍 [Step 1] 감지 대기 자세(Standby)로 이동")
        mc.send_angles(POS_STANDBY, 30)
        wait_until_stop(mc)
        time.sleep(1.0)

        # 단계 2: 충전구 감지 대기
        wait_charge_detected(sock, max_wait_sec=args.detect_wait_sec)

        # 단계 3: 거리 측정 및 계산
        curr_pose = mc.get_coords()
        FIXED_X, start_y, FIXED_Z = curr_pose[0], curr_pose[1], curr_pose[2]
        start_rx, f_ry, f_rz = curr_pose[3], curr_pose[4], curr_pose[5]

        avg_dist = get_average_distance(sock, duration=5.0)
        if avg_dist is None:
            raise RuntimeError("AR 마커 거리 측정 데이터 수집 실패")

        physical_dist_needed = avg_dist - CAM_TO_GRIP_OFFSET - FINAL_TARGET_DIST + EXTRA_Y_OFFSET
        total_move_dist = 0.0 if physical_dist_needed < 0 else physical_dist_needed * GAIN_Y

        print(f"🎯 [Control] 목표 거리: {avg_dist:.1f}mm | 이동량: {total_move_dist:.1f}mm")

        # 단계 4: 정밀 도킹 (Multi-Step with Compensation)
        STEPS = 2
        for i in range(1, STEPS + 1):
            ratio = i / STEPS
            current_delta_y = total_move_dist * ratio
            target_y = start_y + current_delta_y

            # 처짐 보정 및 각도 보정 수식 적용
            target_z = FIXED_Z + (abs(current_delta_y) * SAG_COMPENSATION_RATIO)
            target_rx = start_rx + (TILT_COMPENSATION * ratio)

            # 물리적 도달 한계 체크
            if math.sqrt(FIXED_X**2 + target_y**2 + target_z**2) > ABS_MAX_REACH:
                print("⚠️ [Safety] 도달 한계 초과로 인한 좌표 제한")
                target_y = -math.sqrt(ABS_MAX_REACH**2 - FIXED_X**2 - target_z**2)

            mc.send_coords([FIXED_X, target_y, target_z, target_rx, f_ry, f_rz], 20, 0)
            wait_until_stop(mc)
            time.sleep(0.5)

        print("⚡ [Status] 도킹 성공. 충전 시작.")
        time.sleep(args.charge_wait_sec)
        return "DONE", f"Completed: {robot_id}"

    except Exception as e:
        return "FAILED", str(e)

    finally:
        # 안전 복귀 및 대기 상태 전환
        print("🔄 [System] 임무 종료 후 홈 포지션으로 안전 복귀합니다.")
        try:
            mc.send_angles(POS_RETRACT, 25)
            wait_until_stop(mc)
            mc.send_angles(POS_HOME, 30)
            wait_until_stop(mc)
        except Exception:
            pass
        print("💤 [System] 다음 명령을 기다립니다...\n")
        time.sleep(1.0)


def run_worker_loop(mc: MyCobot280, sock: socket.socket, args: argparse.Namespace) -> None:
    cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cmd_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    cmd_sock.bind((args.worker_bind_ip, args.worker_cmd_port))
    cmd_sock.settimeout(1.0)

    result_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print(
        f"🤖 [Worker] START_CHARGE 작업 대기 중 | bind={args.worker_bind_ip}:{args.worker_cmd_port}"
    )

    while True:
        try:
            raw, _addr = cmd_sock.recvfrom(8192)
        except socket.timeout:
            continue
        except Exception as e:
            print(f"⚠️ [Worker] 명령 수신 오류: {e}")
            continue

        try:
            msg = json.loads(raw.decode(errors="ignore"))
        except Exception:
            print("⚠️ [Worker] JSON 파싱 실패")
            continue

        if str(msg.get("type", "")).upper() != "RUN":
            continue

        job = str(msg.get("job", "")).upper()
        cmd_id = msg.get("cmd_id")
        detail = str(msg.get("detail", ""))
        robot_id = str(msg.get("robot_id", "") or parse_robot_id_from_detail(detail) or "UNKNOWN")

        if job != "START_CHARGE":
            payload = {
                "type": "RESULT",
                "cmd_id": cmd_id,
                "status": "FAILED",
                "detail": f"unsupported_job={job}",
            }
            result_sock.sendto(
                json.dumps(payload, ensure_ascii=False).encode(),
                (args.supervisor_result_ip, args.supervisor_result_port),
            )
            continue

        print(f"🚀 [Worker] START_CHARGE 시작 | robot_id={robot_id} cmd_id={cmd_id}")
        status, result_detail = run_start_charge_mission(mc, sock, args, robot_id)
        payload = {
            "type": "RESULT",
            "cmd_id": cmd_id,
            "status": status,
            "detail": result_detail[:255],
            "robot_id": robot_id,
        }
        result_sock.sendto(
            json.dumps(payload, ensure_ascii=False).encode(),
            (args.supervisor_result_ip, args.supervisor_result_port),
        )
        print(f"📤 [Worker] 결과 전송 | cmd_id={cmd_id} status={status}")


# --- [메인 서비스 루프] ---


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-base", default="http://192.168.1.8:8000")
    ap.add_argument("--client-id", default="jetcobot1")
    ap.add_argument("--vision-pc-ip", default="192.168.1.8")
    ap.add_argument("--poll", type=float, default=1.0)  # 폴링 간격
    ap.add_argument("--charge-wait-sec", type=float, default=10.0)  # 실제 충전 지속 시간
    ap.add_argument("--http-timeout", type=float, default=8.0)
    ap.add_argument("--detect-wait-sec", type=float, default=45.0)
    ap.add_argument("--worker", action="store_true", help="서버 폴링 없이 로컬 워커 모드로 실행")
    ap.add_argument("--worker-bind-ip", default="127.0.0.1")
    ap.add_argument("--worker-cmd-port", type=int, default=18990)
    ap.add_argument("--supervisor-result-ip", default="127.0.0.1")
    ap.add_argument("--supervisor-result-port", type=int, default=18991)
    args = ap.parse_args()

    sess = requests.Session()

    try:
        mc = MyCobot280("/dev/ttyJETCOBOT", 1000000)
        mc.thread_lock = True
        print("✅ [Hardware] MyCobot 280 연결 성공")
    except Exception as e:
        print(f"❌ [Hardware] 로봇 연결 실패: {e}")
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((UDP_IP, UDP_PORT))
    except OSError:
        print("⚠️ [Network] 포트 8888이 이미 사용 중입니다.")
        return

    if args.worker:
        print("🤖 [System] ARM worker 모드로 시작합니다.")
    else:
        print("🤖 [System] 자율 충전 서비스가 활성화되었습니다. 명령 대기 모드로 진입합니다.")

    # 서비스 시작 시 홈 포지션으로 이동
    mc.send_angles(POS_HOME, 30)
    wait_until_stop(mc)

    if args.worker:
        run_worker_loop(mc, sock, args)
        return

    while True:
        cmd, cmd_id, detail = None, None, ""

        # 1. 명령 폴링 (상시 대기)
        try:
            cmd, cmd_id, detail = fetch_next_arm_command(sess, args.server_base, args.client_id, args.http_timeout)
        except Exception as e:
            print(f"📡 [Network] 서버 연결 확인 중... ({e})")
            time.sleep(2.0)
            continue

        if cmd is None or cmd.strip().upper() != "START_CHARGE":
            time.sleep(args.poll)
            continue

        # 2. 임무 수행 (Mission Logic)
        robot_id = parse_robot_id_from_detail(detail) or "UNKNOWN"
        print(f"\n🚀 [Mission] 새로운 충전 임무 수신! 대상 로봇: {robot_id}")

        status, result_detail = run_start_charge_mission(mc, sock, args, robot_id)
        ack_arm_command(
            sess,
            args.server_base,
            args.client_id,
            cmd_id,
            status,
            result_detail,
            args.http_timeout,
        )
        if status == "DONE" and robot_id != "UNKNOWN":
            notify_charge_done(sess, args.server_base, robot_id, args.http_timeout)


if __name__ == "__main__":
    main()
