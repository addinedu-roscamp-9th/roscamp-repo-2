import cv2
import numpy as np
import os
from datetime import datetime

# ==========================
# 1️⃣ 캘리브레이션 값 (FIX)
# ==========================
camera_matrix = np.array([
    [943.84757642, 0.0, 634.62588774],
    [0.0, 944.21061104, 365.53942701],
    [0.0, 0.0, 1.0]
], dtype=np.float32)

dist_coeffs = np.array(
    [[-0.23564148, -0.03986958, 0.01129794, -0.0089482, 0.07595216]],
    dtype=np.float32
)

alpha = 0.4  # 🔥 직선성 최우선 (픽스)

# ==========================
# 2️⃣ ArUco 4x4 설정
# ==========================
aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
aruco_params = cv2.aruco.DetectorParameters()
aruco_detector = cv2.aruco.ArucoDetector(aruco_dict, aruco_params)

MARKER_LENGTH = 15.0  # cm

# ==========================
# 3️⃣ 저장 설정
# ==========================
CAPTURE_DIR = "captures"
os.makedirs(CAPTURE_DIR, exist_ok=True)

saved_banner_until = 0
saved_banner_text = ""

# ==========================
# 4️⃣ 카메라 열기
# ==========================
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

ret, frame = cap.read()
if not ret:
    print("❌ 카메라 프레임 읽기 실패")
    exit()

h, w = frame.shape[:2]

new_camera_matrix, roi = cv2.getOptimalNewCameraMatrix(
    camera_matrix, dist_coeffs, (w, h), alpha, (w, h)
)

# ==========================
# 5️⃣ 메인 루프
# ==========================
print("▶ ArUco 인식 시작 (q 또는 ESC 종료)")
print("▶ 캡쳐: 's' 키 (이미지 + pose txt 저장)")

tick = 0

while True:
    ret, frame = cap.read()
    if not ret:
        break

    # 🔹 왜곡 보정
    undistorted = cv2.undistort(
        frame, camera_matrix, dist_coeffs, None, new_camera_matrix
    )

    # 🔹 ROI 크롭
    x, y, w_roi, h_roi = roi
    undistorted = undistorted[y:y+h_roi, x:x+w_roi]

    # 🔹 ArUco 인식
    corners, ids, _ = aruco_detector.detectMarkers(undistorted)

    rvecs = tvecs = None
    if ids is not None:
        cv2.aruco.drawDetectedMarkers(undistorted, corners, ids)

        rvecs, tvecs, _ = cv2.aruco.estimatePoseSingleMarkers(
            corners,
            MARKER_LENGTH,
            new_camera_matrix,
            dist_coeffs
        )

        for i in range(len(ids)):
            cv2.drawFrameAxes(
                undistorted,
                new_camera_matrix,
                dist_coeffs,
                rvecs[i],
                tvecs[i],
                MARKER_LENGTH * 0.5
            )

            tx, ty, tz = tvecs[i][0]
            cv2.putText(
                undistorted,
                f"ID {ids[i][0]}  X:{tx:.1f} Y:{ty:.1f} Z:{tz:.1f} cm",
                (10, 30 + i * 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2
            )

    # 🔹 저장 배너
    if tick <= saved_banner_until and saved_banner_text:
        cv2.putText(
            undistorted,
            saved_banner_text,
            (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2
        )

    cv2.imshow("Undistorted + ArUco", undistorted)

    key = cv2.waitKey(1) & 0xFF

    # ==========================
    # ✅ 캡쳐: 이미지 + pose txt
    # ==========================
    if key == ord('s'):
        now = datetime.now()
        base = now.strftime("aruco_%Y%m%d_%H%M%S_%f")[:-3]
        img_path = os.path.join(CAPTURE_DIR, base + ".png")
        txt_path = os.path.join(CAPTURE_DIR, base + ".txt")

        # 이미지 저장
        ok_img = cv2.imwrite(img_path, undistorted)

        # pose 텍스트 저장
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"timestamp: {now.isoformat()}\n")
            f.write("units: translation=cm, rotation=radians\n")
            f.write(f"marker_length_cm: {MARKER_LENGTH}\n")
            f.write(f"alpha: {alpha}\n")
            f.write("camera_matrix:\n")
            f.write(np.array2string(camera_matrix, precision=6) + "\n")
            f.write("dist_coeffs:\n")
            f.write(np.array2string(dist_coeffs, precision=6) + "\n")
            f.write("\n")

            if ids is None or rvecs is None or tvecs is None:
                f.write("markers: none\n")
            else:
                f.write(f"markers: {len(ids)}\n")
                for i in range(len(ids)):
                    rv = rvecs[i][0]
                    tv = tvecs[i][0]
                    f.write(
                        f"- id: {int(ids[i][0])}\n"
                        f"  tvec_cm: [X={tv[0]:.3f}, Y={tv[1]:.3f}, Z={tv[2]:.3f}]\n"
                        f"  rvec_rad: [rx={rv[0]:.6f}, ry={rv[1]:.6f}, rz={rv[2]:.6f}]\n"
                    )

        if ok_img:
            print(f"✅ SAVED: {img_path}")
            print(f"✅ SAVED: {txt_path}")
            saved_banner_text = f"SAVED: {base}.png + .txt"
            saved_banner_until = tick + 60
        else:
            print("❌ 이미지 저장 실패")

    if key == ord('q') or key == 27:
        break

    tick += 1

cap.release()
cv2.destroyAllWindows()

