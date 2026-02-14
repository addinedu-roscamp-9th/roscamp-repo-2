import cv2
import numpy as np
import socket
import time
import os
from ultralytics import YOLO

# =========================================================
# 1. 설정 및 초기화
# =========================================================
current_mode = 0 
ROBOT_MOVE_TIME = 1.0 

# [YOLO 모델 설정]
# 학습시킨 모델 경로가 맞는지 꼭 확인해주세요! (예: runs/detect/train_charge/...)
model_path = "runs/detect/train/weights/best.pt"
if os.path.exists(model_path):
    print(f"✅ 커스텀 YOLO 모델 로드: {model_path}")
    model = YOLO(model_path)
else:
    print("⚠️ 커스텀 모델 없음, 기본 모델 사용")
    model = YOLO("yolov8n.pt")

# 🔥 [핵심 수정 1] 타겟 이름 변경 (소문자 권장)
TARGET_CLASS_NAME = "charge" 

# [카메라 매트릭스]
try:
    mtx = np.load("camera_matrix.npy")
    dist = np.load("dist_coeffs.npy")
except:
    mtx = np.array([[600, 0, 320], [0, 600, 240], [0, 0, 1]], dtype=float)
    dist = np.zeros(5)

MARKER_LENGTH = 0.03

# [통신 설정 - 송신] (데이터 -> 로봇)
JETCOBOT_IP = "192.168.5.1"
JETCOBOT_PORT = 8888
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# [통신 설정 - 수신] (명령 <- 로봇)
CMD_PORT = 8889
cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
cmd_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    cmd_sock.bind(("0.0.0.0", CMD_PORT))
    cmd_sock.setblocking(False)
    print(f"👂 로봇 명령 대기 중 (Port {CMD_PORT})...")
except Exception as e:
    print(f"❌ 소켓 바인딩 에러: {e}")

# [카메라 설정]
CAMERA_PORT = 5000
gst_pipeline = (
    f"udpsrc port={CAMERA_PORT} caps=application/x-rtp ! "
    "rtpjpegdepay ! jpegdec ! videoconvert ! "
    "appsink drop=true sync=false"
)
try:
    cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
    if not cap.isOpened(): raise Exception
except:
    print("⚠️ GStreamer 실패, 기본 카메라(0) 사용")
    cap = cv2.VideoCapture(0)

# [파라미터]
CONF_THRESH = 0.5
# 🔥 [핵심 수정 2] 인식 유지 시간 5초로 변경
HOLD_TIME = 3.0 

detect_start_time = None
mode_switch_time = None 
buffer = []
BUFF_SIZE = 5

# [ArUco 설정]
aruco_dict = cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50)
aruco_params = cv2.aruco.DetectorParameters_create()

print(f"🚀 시스템 가동 (Target: {TARGET_CLASS_NAME})")

# =========================================================
# 2. 메인 루프
# =========================================================
while True:
    ret, frame = cap.read()
    if not ret: break

    h, w = frame.shape[:2]
    newcameramtx, _ = cv2.getOptimalNewCameraMatrix(mtx, dist, (w, h), 0)
    undistorted = cv2.undistort(frame, mtx, dist, None, newcameramtx)
    
    # 🔄 [리셋 명령 수신]
    try:
        data, _ = cmd_sock.recvfrom(1024)
        if b"RESET" in data:
            print("\n🔄 [RESET] 로봇 요청으로 초기화!")
            current_mode = 0
            detect_start_time = None
            buffer = []
    except: pass

    # -----------------------------------------------------
    # MODE 0: YOLO Charge 탐색
    # -----------------------------------------------------
    if current_mode == 0:
        results = model(undistorted, conf=CONF_THRESH, verbose=False)
        detected = False
        now = time.time()
        status_text = "MODE: YOLO Scanning..."

        # 객체 감지 루프
        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            label = model.names[cls_id]
            conf_score = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            
            # 타겟('charge')인 경우
            if label == TARGET_CLASS_NAME:
                detected = True
                # ✅ 선명한 초록색 + 두껍게
                cv2.rectangle(undistorted, (x1, y1), (x2, y2), (0, 255, 0), 4)
                
                display_text = f"{label} {conf_score:.2f}"
                cv2.putText(undistorted, display_text, (x1, y1 - 15), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 3)
            else:
                # 비타겟: 빨간색 + 얇게
                cv2.rectangle(undistorted, (x1, y1), (x2, y2), (0, 0, 255), 2)
                display_text = f"{label} {conf_score:.2f}"
                cv2.putText(undistorted, display_text, (x1, y1 - 10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        # 타이머 로직
        if detected:
            if detect_start_time is None:
                detect_start_time = now
            else:
                elapsed = now - detect_start_time
                status_text = f"Holding: {elapsed:.1f}/{HOLD_TIME}s" # 진행 상황 표시
                
                if elapsed >= HOLD_TIME:
                    print(f"✅ {TARGET_CLASS_NAME} 5초 인식 완료 -> 로봇 전송")
                    # 🔥 [핵심 수정 3] 신호 내용 변경
                    sock.sendto(b"CHARGE_DETECTED", (JETCOBOT_IP, JETCOBOT_PORT))
                    current_mode = 1 
                    mode_switch_time = now
        else:
            detect_start_time = None

    # -----------------------------------------------------
    # MODE 1: 대기 (로봇 이동 중)
    # -----------------------------------------------------
    elif current_mode == 1:
        elapsed = time.time() - mode_switch_time
        remaining = ROBOT_MOVE_TIME - elapsed
        status_text = f"MODE: Switching... (Wait {remaining:.1f}s)"
        if elapsed > ROBOT_MOVE_TIME:
            current_mode = 2
            buffer = [] 

    # -----------------------------------------------------
    # MODE 2: ArUco 추적
    # -----------------------------------------------------
    elif current_mode == 2:
        status_text = "MODE: ArUco Tracking"
        corners, ids, _ = cv2.aruco.detectMarkers(undistorted, aruco_dict, parameters=aruco_params)
        if ids is not None:
            rvecs, tvecs, _ = cv2.aruco.estimatePoseSingleMarkers(corners, MARKER_LENGTH, mtx, dist)
            tvec = tvecs[0][0] * 1000 
            buffer.append(tvec)
            if len(buffer) > BUFF_SIZE: buffer.pop(0)
            avg_tvec = np.mean(buffer, axis=0)
            
            msg = f"AR,{avg_tvec[0]:.2f},{avg_tvec[1]:.2f},{avg_tvec[2]:.2f}"
            sock.sendto(msg.encode(), (JETCOBOT_IP, JETCOBOT_PORT))
            
            cv2.aruco.drawDetectedMarkers(undistorted, corners, ids)
            cv2.drawFrameAxes(undistorted, mtx, dist, rvecs[0], tvecs[0], 0.03)
            
            # ArUco 좌표
            coord_text = f"X:{avg_tvec[0]:.1f} Y:{avg_tvec[1]:.1f} Z:{avg_tvec[2]:.1f}"
            cv2.putText(undistorted, coord_text, (int(corners[0][0][0][0]), int(corners[0][0][0][1]) - 20), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 3)

    # 상태 텍스트 (빨간색, 작게)
    cv2.putText(undistorted, status_text, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.imshow("Smart Vision System", undistorted)
    
    if cv2.waitKey(1) & 0xFF == ord('q'): break

cap.release()
cv2.destroyAllWindows()
cmd_sock.close()
sock.close()

