#!/usr/bin/env python3
import json
import cv2
import numpy as np

# ==========================
# ✅ 새 카메라 캘리브레이션 값 (사용자 제공)
# ==========================
camera_matrix = np.array([
    [1283.03181, 0.0, 973.421613],
    [0.0, 1293.41663, 559.521555],
    [0.0, 0.0, 1.0]
], dtype=np.float32)

dist_coeffs = np.array(
    [[-0.18392985, 0.01382315, 0.00725583, 0.00318795, 0.05317553]],
    dtype=np.float32
)

alpha = 0.4

# ==========================
# 캡처 설정
# ==========================
DEVICE_INDEX = 0
REQ_W, REQ_H = 1928, 1080

WINDOW = "Homography Calibrate (CLICK 4 pts: BL->TL->TR->BR) | r=reset, s=save, q/ESC=quit"
OUT_JSON = "homography.json"

clicked_pts = []  # undistort된 전체 프레임 좌표 (u,v)
newK = None
last_und = None


def clamp_roi(x, y, w, h, W, H):
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(W, int(x + w))
    y1 = min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        raise RuntimeError("Invalid ROI computed.")
    return x0, y0, (x1 - x0), (y1 - y0)


def compute_roi_from_points(pts, margin=10):
    us = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    x0 = min(us) - margin
    y0 = min(vs) - margin
    x1 = max(us) + margin
    y1 = max(vs) + margin
    return x0, y0, (x1 - x0), (y1 - y0)


def mouse_cb(event, x, y, flags, param):
    global clicked_pts
    if event == cv2.EVENT_LBUTTONDOWN:
        if len(clicked_pts) >= 4:
            print("[INFO] 이미 4점이 선택되었습니다. r로 초기화하세요.")
            return
        clicked_pts.append([float(x), float(y)])
        order = ["BL(0,0)", "TL(0,1)", "TR(2,1)", "BR(2,0)"]
        print(f"[CLICK] {len(clicked_pts)}/4: (u,v)=({x},{y})  -> {order[len(clicked_pts)-1]}")


def draw_overlay(img, pts):
    draw = img.copy()
    labels = ["BL", "TL", "TR", "BR"]
    for i, (u, v) in enumerate(pts):
        cv2.circle(draw, (int(u), int(v)), 6, (0, 0, 255), -1)
        cv2.putText(draw, labels[i], (int(u) + 8, int(v) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

    if len(pts) == 4:
        p = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(draw, [p], isClosed=True, color=(0, 255, 0), thickness=2)
    return draw


def main():
    global newK, last_und

    cap = cv2.VideoCapture(DEVICE_INDEX, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(DEVICE_INDEX)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, REQ_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, REQ_H)

    ret, frame = cap.read()
    if not ret or frame is None:
        raise RuntimeError("Camera error: cannot read first frame")

    H0, W0 = frame.shape[:2]
    print(f"[CAM] requested={REQ_W}x{REQ_H} | actual={W0}x{H0}")

    newK, roi_runtime = cv2.getOptimalNewCameraMatrix(
        camera_matrix, dist_coeffs, (W0, H0), alpha, (W0, H0)
    )
    print(f"[UNDISTORT] alpha={alpha}, runtime_roi(OpenCV 참고용)={roi_runtime}")

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW, mouse_cb)

    print("\n[INSTRUCTION]")
    print("  4점을 반드시 아래 순서로 클릭하세요 (원점=좌하단, 2m x 1m):")
    print("    1) BL (0,0)  좌하단")
    print("    2) TL (0,1)  좌상단")
    print("    3) TR (2,1)  우상단")
    print("    4) BR (2,0)  우하단")
    print("  완료 후 s 저장 / r 리셋 / q 또는 ESC 종료\n")

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            continue

        und = cv2.undistort(frame, camera_matrix, dist_coeffs, None, newK)
        last_und = und

        view = draw_overlay(und, clicked_pts)
        cv2.imshow(WINDOW, view)

        k = cv2.waitKey(1) & 0xFF
        if k in (27, ord('q')):
            break

        if k == ord('r'):
            clicked_pts.clear()
            print("[RESET] clicked points cleared.")

        if k == ord('s'):
            if len(clicked_pts) != 4:
                print("[ERROR] 4점을 모두 클릭해야 저장할 수 있습니다.")
                continue

            # 1) ROI 계산 (undistorted 전체 프레임 기준)
            roi_x, roi_y, roi_w, roi_h = compute_roi_from_points(clicked_pts, margin=10)
            roi_x, roi_y, roi_w, roi_h = clamp_roi(roi_x, roi_y, roi_w, roi_h, W0, H0)
            print(f"[ROI] computed: x={roi_x}, y={roi_y}, w={roi_w}, h={roi_h}")

            # 2) ROI 기준 클릭점 좌표로 변환
            roi_pts = []
            for (u, v) in clicked_pts:
                roi_pts.append([u - roi_x, v - roi_y])

            # 3) ✅ world 좌표 고정 (meter): BL,TL,TR,BR
            world_pts = np.array([
                [0.0, 0.0],  # BL
                [0.0, 1.0],  # TL
                [2.0, 1.0],  # TR
                [2.0, 0.0],  # BR
            ], dtype=np.float32)

            # 4) Homography 계산: ROI pixel -> world(m)
            src = np.array(roi_pts, dtype=np.float32)
            dst = world_pts
            Hm, _ = cv2.findHomography(src, dst, method=0)
            if Hm is None:
                print("[ERROR] Homography 계산 실패. 점이 일직선/순서 오류일 수 있습니다.")
                continue

            data = {
                "H": Hm.tolist(),
                "roi": {"x": int(roi_x), "y": int(roi_y), "w": int(roi_w), "h": int(roi_h)},
                "note": "pixel(undistorted+cropped ROI) -> world(map, meter) | field=2.0m x 1.0m | origin=BL"
            }

            with open(OUT_JSON, "w") as f:
                json.dump(data, f, indent=2)

            print(f"[SAVED] {OUT_JSON}")
            print("[INFO] 이제 이 homography.json은 진짜 meter(2.0 x 1.0) 좌표계입니다.")
            # 저장 후 종료 원하면 주석 해제
            # break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

