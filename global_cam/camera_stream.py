import cv2
import numpy as np


class CameraStream:
    """
    - getOptimalNewCameraMatrix로 newK 계산
    - undistort(frame) 한 뒤
    - ROI는 homography.json 기준으로 강제 적용하여 crop
    """

    def __init__(
        self,
        device_index: int,
        width: int,
        height: int,
        camera_matrix: np.ndarray,
        dist_coeffs: np.ndarray,
        alpha: float,
        roi_x: int,
        roi_y: int,
        roi_w: int,
        roi_h: int,
    ):
        self.device_index = int(device_index)
        self.width = int(width)
        self.height = int(height)
        self.camera_matrix = camera_matrix.astype(np.float32)
        self.dist_coeffs = dist_coeffs.astype(np.float32)
        self.alpha = float(alpha)

        self.roi_x = int(roi_x)
        self.roi_y = int(roi_y)
        self.roi_w = int(roi_w)
        self.roi_h = int(roi_h)

        self.cap = None
        self.newK = None
        self._opened = False

        # 적용 ROI(undistorted 기준)
        self.x0 = self.y0 = self.x1 = self.y1 = None

    def open(self):
        # V4L2 우선 (리눅스에서 설정 반영이 더 잘 되는 경우가 많음)
        self.cap = cv2.VideoCapture(self.device_index, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(self.device_index)

        # 원하는 해상도 요청
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        if not self.cap.isOpened():
            raise RuntimeError("Camera error: cannot open")

        ret, frame = self.cap.read()
        if not ret or frame is None:
            raise RuntimeError("Camera error: cannot read first frame")

        h, w = frame.shape[:2]
        print(f"[CAM] requested: {self.width}x{self.height} | actual: {w}x{h}")

        self.newK, roi_runtime = cv2.getOptimalNewCameraMatrix(
            self.camera_matrix, self.dist_coeffs, (w, h), self.alpha, (w, h)
        )

        # ROI는 homography.json 기준으로 강제
        x0 = max(0, self.roi_x)
        y0 = max(0, self.roi_y)
        x1 = min(w, self.roi_x + self.roi_w if self.roi_w > 0 else w)
        y1 = min(h, self.roi_y + self.roi_h if self.roi_h > 0 else h)

        if x1 <= x0 or y1 <= y0:
            raise RuntimeError("[ERROR] Invalid ROI from homography.json. Check roi fields.")

        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

        print(f"[ROI] runtime roi(from OpenCV) = {roi_runtime} (참고용)")
        print(f"[ROI] applied roi(from homography.json) = x={x0}, y={y0}, w={x1-x0}, h={y1-y0}")

        self._opened = True

    def read_roi(self) -> tuple[np.ndarray | None, bool]:
        if not self._opened or self.cap is None:
            return None, False

        ret, frame = self.cap.read()
        if not ret or frame is None:
            return None, False

        und = cv2.undistort(frame, self.camera_matrix, self.dist_coeffs, None, self.newK)
        roi = und[self.y0:self.y1, self.x0:self.x1]
        return roi, True

    def release(self):
        if self.cap is not None:
            self.cap.release()
        self.cap = None
        self._opened = False
