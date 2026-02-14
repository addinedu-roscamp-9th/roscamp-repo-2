import socket
import time
import numpy as np
import math
from pymycobot.mycobot280 import MyCobot280

# =========================================================
# 1. 파라미터 설정
# =========================================================
UDP_IP, UDP_PORT = "0.0.0.0", 8888
ABS_MAX_REACH = 275.0 

GAIN_Y = -1.0 
CAM_TO_GRIP_OFFSET = 50.0   
FINAL_TARGET_DIST = 10.0   
# 🔥 [보정] -y 방향으로 1cm(10mm) 더 움직이게 하는 오프셋
EXTRA_Y_OFFSET = 4.5 

SAG_COMPENSATION_RATIO = 0.02
TILT_COMPENSATION = 0.3

# [자세 설정]
POS_STANDBY = [-135.25, -15.92, -130, -40.74, 133.15, -145.55]
# 중간 복귀자세
POS_RETRACT = [-135.25, -15.92, -130, -40.74, 133.15, -145.55]

try:
    mc = MyCobot280('/dev/ttyJETCOBOT', 1000000)
    mc.thread_lock = True
    print("✅ MyCobot 연결 완료")
except:
    print("❌ 로봇 연결 실패"); exit()

def wait_until_stop():
    time.sleep(0.1)
    while mc.is_moving(): time.sleep(0.1)

def flush_udp_buffer(sock):
    sock.setblocking(False)
    try:
        while True: sock.recvfrom(1024)
    except: pass
    print("🧹 소켓 버퍼 초기화 완료")

def get_average_distance(sock, duration=5.0):
    start_time = time.time()
    buffer_dz = []
    print(f"📏 [측정] {duration}초간 데이터 수집 중...")
    flush_udp_buffer(sock) 
    sock.setblocking(True)
    while (time.time() - start_time) < duration:
        try:
            sock.settimeout(0.2)
            data, _ = sock.recvfrom(1024)
            msg = data.decode()
            if msg.startswith("AR,"):
                vals = [float(v) for v in msg.split(',')[1:]]
                if 10.0 < vals[2] < 1000.0: buffer_dz.append(vals[2])
        except: continue
    return np.mean(buffer_dz) if buffer_dz else None

# =========================================================
# 2. 메인 실행
# =========================================================
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

try:
    sock.bind((UDP_IP, UDP_PORT))
except OSError:
    print("⚠️ 포트 점유 중. 잠시 후 다시 시도하세요."); exit()

try:
    # -----------------------------------------------------
    # Phase 1: 즉시 대기 자세 이동 & 충전구(Charge) 탐색
    # -----------------------------------------------------
    print("\n📍 Phase 1: 충전 대기 자세로 이동합니다...")
    mc.send_angles(POS_STANDBY, 30)
    wait_until_stop()
    
    time.sleep(3.0)
    flush_udp_buffer(sock)
    print("📡 'charge' 인식 대기 중... (YOLO)")
    
    while True:
        try:
            data, _ = sock.recvfrom(1024)
            if b"CHARGE_DETECTED" in data: 
                print("🎯 충전구(Charge) 확인됨! 도킹 시퀀스 시작.")
                break
        except: time.sleep(5.0)
    
    # -----------------------------------------------------
    # Phase 2: ArUco 기반 정밀 진입
    # -----------------------------------------------------
    curr_pose = mc.get_coords()
    FIXED_X, start_y, FIXED_Z = curr_pose[0], curr_pose[1], curr_pose[2]
    start_rx, f_ry, f_rz = curr_pose[3], curr_pose[4], curr_pose[5]
    
    print(f"🔒 기준 축 설정: X={FIXED_X:.1f}, Z={FIXED_Z:.1f}, Rx={start_rx:.1f}")
    
    avg_dist = get_average_distance(sock, duration=5.0)
    if avg_dist is None: print("❌ 측정 실패"); exit()

    # 🔥 [수식 보정] physical_dist_needed에 EXTRA_Y_OFFSET을 더해 더 전진하게 함
    # 10mm를 더함으로써 total_move_dist의 음수값이 더 커지게 됩니다 (-y 방향 전진).
    physical_dist_needed = avg_dist - CAM_TO_GRIP_OFFSET - FINAL_TARGET_DIST + EXTRA_Y_OFFSET
    
    if physical_dist_needed < 0: total_move_dist = 0.0
    else: total_move_dist = physical_dist_needed * GAIN_Y

    print(f"🎯 측정거리: {avg_dist:.1f}mm / 보정된 목표이동: {total_move_dist:.1f}mm")

    STEPS = 2
    for i in range(1, STEPS + 1):
        ratio = i / STEPS
        current_delta_y = total_move_dist * ratio
        target_y = start_y + current_delta_y
        
        target_z = FIXED_Z + (abs(current_delta_y) * SAG_COMPENSATION_RATIO)
        target_rx = start_rx + (TILT_COMPENSATION * ratio)
        
        # 기하학적 클램핑 (안전장치)
        dist_sq = FIXED_X**2 + target_y**2 + target_z**2
        max_r_sq = ABS_MAX_REACH**2 
        
        if dist_sq > max_r_sq:
            available_y_sq = max_r_sq - FIXED_X**2 - target_z**2
            if available_y_sq > 0:
                max_possible_y = math.sqrt(available_y_sq)
                target_y = -max_possible_y 
                print(f"⚠️ [거리 제한] Y좌표 보정 -> {target_y:.1f}")
            else:
                print("🛑 [이동 불가] 한계 초과"); break

        print(f"   Step {i}: Y={target_y:.1f}, Z={target_z:.1f}, Rx={target_rx:.1f}")
        mc.send_coords([FIXED_X, target_y, target_z, target_rx, f_ry, f_rz], 20, 0)
        wait_until_stop()
        time.sleep(1.0) 

    print("\n✨ 도킹 완료.")
    
    # -----------------------------------------------------
    # Phase 3: 충전 후 복귀
    # -----------------------------------------------------
    print("⚡ 충전 중... (5초)")
    time.sleep(5.0)

    # 중간 복귀자세 요청 사항 반영
    print("🔄 1차 복귀 자세로 이동 중...")
    mc.send_angles(POS_RETRACT, 25) 
    wait_until_stop()
    time.sleep(1.0)
    
    print("🚀 초기 위치(0,0,0,0,0,0)로 복귀합니다.")
    mc.send_angles([0, 0, 0, 0, 0, 0], 30)
    wait_until_stop()
    
    print("🏁 미션 종료.")

except Exception as e:
    print(f"\n❌ 에러: {e}"); mc.stop()
finally:
    sock.close()
