import argparse
import socket
import time
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

# =========================
# 기본 설정 (기존 로직 유지)
# =========================
ROBOT_MOVE_TIME = 1.0
CONF_THRESH = 0.3
HOLD_TIME = 2.0
MARKER_LENGTH = 0.03  # meter

TARGET_CLASS_NAME = "charge"

BUFF_SIZE = 5


def build_rx_pipeline_h264(port: int, payload: int = 96) -> str:
    # RTP/H264 -> decode -> BGR -> appsink
    return (
        f'udpsrc port={port} caps="application/x-rtp,media=video,encoding-name=H264,payload={payload}" ! '
        "rtpjitterbuffer latency=0 drop-on-latency=true ! "
        "rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! "
        "video/x-raw,format=BGR ! "
        "appsink drop=true sync=false max-buffers=1"
    )


def build_tx_pipeline_h264(
    host: str,
    port: int,
    width: int,
    height: int,
    fps: int,
    payload: int = 96,
    bitrate_kbps: int = 1200,
) -> str:
    # appsrc(BGR) -> x264enc -> RTP/H264 -> udpsink
    # OpenCV VideoWriter with CAP_GSTREAMER uses appsrc internally when you provide a pipeline starting with appsrc.
    return (
        "appsrc is-live=true block=true format=time do-timestamp=true ! "
        f"video/x-raw,format=BGR,width={width},height={height},framerate={fps}/1 ! "
        "videoconvert ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate_kbps} key-int-max=10 bframes=0 ! "
        "h264parse ! "
        f"rtph264pay config-interval=1 pt={payload} mtu=1200 ! "
        f"udpsink host={host} port={port} sync=false async=false"
    )


def send_udp_burst(
    sock: socket.socket, msg: bytes, ip: str, port: int, n: int = 5, gap: float = 0.1
):
    for _ in range(n):
        sock.sendto(msg, (ip, port))
        time.sleep(gap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--model",
        default="/home/cwj/roscamp-repo-2/git/server/ai_server/models/best.pt",
    )
    ap.add_argument(
        "--in-port", type=int, default=5003, help="원본 카메라 입력 포트 (RTP/H264)"
    )
    ap.add_argument(
        "--out-host", default="192.168.1.8", help="관리자GUI가 실행 중인 PC IP"
    )
    ap.add_argument(
        "--out-port", type=int, default=5002, help="관리자GUI ARM 포트(고정)"
    )
    ap.add_argument(
        "--payload", type=int, default=96, help="RTP payload type (H264 기본 96)"
    )
    ap.add_argument("--bitrate", type=int, default=1200, help="H264 bitrate(kbps)")
    ap.add_argument(
        "--fps", type=int, default=30, help="재송출 fps (입력과 다를 수 있음)"
    )
    ap.add_argument("--jetcobot-ip", default="192.168.1.21")
    ap.add_argument("--jetcobot-port", type=int, default=8888)
    ap.add_argument("--cmd-port", type=int, default=8889)
    ap.add_argument(
        "--no-imshow", action="store_true", help="Smart Vision 창 띄우지 않기(운영용)"
    )
    args = ap.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"❌ best.pt 파일이 없습니다: {model_path}")
        raise SystemExit(1)

    print(f"✅ 커스텀 YOLO 모델 로드: {model_path}")
    model = YOLO(str(model_path))

    # 카메라 캘리브 (기존 유지)
    mtx_path = model_path.parent / "camera_matrix.npy"
    dist_path = model_path.parent / "dist_coeffs.npy"
    if not mtx_path.exists() or not dist_path.exists():
        print("❌ camera_matrix.npy / dist_coeffs.npy 파일이 없습니다.")
        raise SystemExit(1)

    mtx = np.load(str(mtx_path))
    dist = np.load(str(dist_path))
    print("✅ 카메라 매트릭스 로드 완료")
    print("✅ 왜곡계수 로드 완료")

    # -------------------------
    # 입력 스트림(5003) 오픈
    # -------------------------
    rx_pipeline = build_rx_pipeline_h264(args.in_port, payload=args.payload)
    cap = cv2.VideoCapture(rx_pipeline, cv2.CAP_GSTREAMER)
    print(f"[DBG] rx_pipeline = {rx_pipeline}", flush=True)
    print("[DBG] after VideoCapture()", flush=True)
    print(f"[DBG] cap.isOpened() = {cap.isOpened()}", flush=True)

    if not cap.isOpened():
        print(f"❌ UDP(H264) 카메라 오픈 실패 (port={args.in_port})")
        raise SystemExit(1)

    print("[DBG] before first cap.read()", flush=True)

    # ✅ 첫 프레임: 최대 3초 타임아웃 (블로킹 방지)
    t0 = time.time()
    ok, frame0 = False, None
    while time.time() - t0 < 3.0:
        ok, frame0 = cap.read()
        if ok and frame0 is not None:
            break
        time.sleep(0.02)

    print(
        f"[DBG] first cap.read() => ok={ok}, frame_is_none={frame0 is None}", flush=True
    )

    if not ok or frame0 is None:
        print("❌ 첫 프레임 수신 실패 (3초 타임아웃) - in-port 스트림/캡스 확인 필요")
        raise SystemExit(1)

    h0, w0 = frame0.shape[:2]
    print(f"✅ 입력 스트림 연결 성공: {w0}x{h0}")

    # -------------------------
    # 출력 스트림(5002) 오픈
    # -------------------------
    tx_pipeline = build_tx_pipeline_h264(
        args.out_host,
        args.out_port,
        w0,
        h0,
        args.fps,
        payload=args.payload,
        bitrate_kbps=args.bitrate,
    )
    writer = cv2.VideoWriter(
        tx_pipeline, cv2.CAP_GSTREAMER, 0, float(args.fps), (w0, h0), True
    )

    if not writer.isOpened():
        print("❌ 재송출 VideoWriter 오픈 실패")
        print("   - OpenCV가 GStreamer 지원으로 빌드됐는지 확인 필요")
        raise SystemExit(1)

    print(
        f"✅ 재송출 준비 완료: {args.out_host}:{args.out_port} (RTP/H264 pt={args.payload})"
    )

    # JetCobot으로 신호 보내는 UDP(기존 유지)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Vision RESET 수신(기존 유지)
    cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cmd_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    cmd_sock.bind(("0.0.0.0", args.cmd_port))
    cmd_sock.setblocking(False)
    print(f"👂 로봇 명령 대기 중 (Port {args.cmd_port})...")

    # ArUco 준비(기존 유지)
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    use_new_aruco = False
    detector = None
    aruco_params = None

    if hasattr(cv2.aruco, "ArucoDetector") and hasattr(cv2.aruco, "DetectorParameters"):
        try:
            aruco_params = cv2.aruco.DetectorParameters()
            detector = cv2.aruco.ArucoDetector(aruco_dict, aruco_params)
            use_new_aruco = True
            print("✅ ArUco: 신 API(ArucoDetector) 사용")
        except Exception:
            use_new_aruco = False

    if not use_new_aruco:
        if hasattr(cv2.aruco, "DetectorParameters_create"):
            aruco_params = cv2.aruco.DetectorParameters_create()
        else:
            aruco_params = cv2.aruco.DetectorParameters()
        print("✅ ArUco: 구 API(detectMarkers) 사용")

    print("🚀 시스템 시작")

    current_mode = 0
    detect_start_time = None
    mode_switch_time = None
    buffer = []

    def send_charge_detected_burst():
        send_udp_burst(
            sock, b"CHARGE_DETECTED", args.jetcobot_ip, args.jetcobot_port, n=5, gap=0.1
        )
        print("✅ CHARGE_DETECTED 전송(버스트 5회)")

    # 첫 프레임을 다시 쓰기 위해 루프에 포함
    frame = frame0

    while True:
        if frame is None:
            ret, frame = cap.read()
            if not ret:
                print("❌ 영상 수신 실패")
                break
        else:
            # frame0를 1회만 사용
            ret = True

        # 왜곡 보정
        h, w = frame.shape[:2]
        newcameramtx, _ = cv2.getOptimalNewCameraMatrix(mtx, dist, (w, h), 0)
        undistorted = cv2.undistort(frame, mtx, dist, None, newcameramtx)

        # RESET 수신 처리
        try:
            data, _ = cmd_sock.recvfrom(1024)
            if b"RESET" in data:
                print("🔄 RESET 수신")
                current_mode = 0
                detect_start_time = None
                buffer = []
        except Exception:
            pass

        status_text = ""

        if current_mode == 0:
            results = model(undistorted, conf=CONF_THRESH, verbose=False)
            detected = False
            now = time.time()
            status_text = "MODE: YOLO"

            for box in results[0].boxes:
                cls_id = int(box.cls[0])
                label = model.names[cls_id]
                conf_score = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0])

                if label == TARGET_CLASS_NAME:
                    detected = True
                    cv2.rectangle(undistorted, (x1, y1), (x2, y2), (0, 255, 0), 4)
                    cv2.putText(
                        undistorted,
                        f"{label} {conf_score:.2f}",
                        (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 0),
                        2,
                    )

            if detected:
                if detect_start_time is None:
                    detect_start_time = now
                elif now - detect_start_time >= HOLD_TIME:
                    send_charge_detected_burst()
                    current_mode = 1
                    mode_switch_time = now
            else:
                detect_start_time = None

        elif current_mode == 1:
            if time.time() - mode_switch_time > ROBOT_MOVE_TIME:
                current_mode = 2
                buffer = []
            status_text = "MODE: Switching"

        elif current_mode == 2:
            status_text = "MODE: ArUco"

            if use_new_aruco and detector is not None:
                corners, ids, _ = detector.detectMarkers(undistorted)
            else:
                corners, ids, _ = cv2.aruco.detectMarkers(
                    undistorted, aruco_dict, parameters=aruco_params
                )

            if ids is not None and len(ids) > 0:
                rvecs, tvecs, _ = cv2.aruco.estimatePoseSingleMarkers(
                    corners, MARKER_LENGTH, mtx, dist
                )

                tvec = tvecs[0][0] * 1000.0
                buffer.append(tvec)
                if len(buffer) > BUFF_SIZE:
                    buffer.pop(0)

                avg = np.mean(buffer, axis=0)
                msg = f"AR,{avg[0]:.2f},{avg[1]:.2f},{avg[2]:.2f}"
                sock.sendto(msg.encode(), (args.jetcobot_ip, args.jetcobot_port))

                cv2.aruco.drawDetectedMarkers(undistorted, corners, ids)
                cv2.drawFrameAxes(undistorted, mtx, dist, rvecs[0], tvecs[0], 0.03)

        cv2.putText(
            undistorted,
            status_text,
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 0, 255),
            2,
        )

        # ✅ 관리자GUI에 보일 "오버레이 프레임"을 5002로 송출
        writer.write(undistorted)

        # (선택) 로컬 디버그 창
        if not args.no_imshow:
            cv2.imshow("Smart Vision", undistorted)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        # 다음 프레임으로
        frame = None

    cap.release()
    writer.release()
    cmd_sock.close()
    sock.close()
    if not args.no_imshow:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
