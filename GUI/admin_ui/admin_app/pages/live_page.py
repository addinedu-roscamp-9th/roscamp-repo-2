# admin_app/pages/live_page.py
# REMASTER v2.6
# - LOW-LATENCY / NO-YOLO / NO-ARUCO
# - GLOBAL: ROI crop ONLY + mode="fit" (토글 전/후 프레이밍 고정)
# - ROI 미세조정 핫키 + 크롭 결과에 테두리 표시(노란색)
#
# HOTKEY (LivePage가 포커스일 때):
#   Arrow Left/Right : x -=/+ step
#   Arrow Up/Down    : y -=/+ step
#   Shift + L/R      : w -=/+ step
#   Shift + U/D      : h -=/+ step
#   step: 기본 1, Alt=5, Ctrl=10
#   S: save ROI to homography.json
#   R: reload ROI from homography.json

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PyQt6 import uic
from PyQt6.QtCore import QEvent, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QLabel, QStackedWidget, QWidget

from ..utils.paths import ui_path
from ..workers.gst_receiver import GstReceiver
from .live_render import show_frame

ROI_BASE_W = 1920
ROI_BASE_H = 1080


@dataclass
class _FpsMeter:
    count: int = 0
    last_t: float = 0.0
    fps: float = 0.0

    def tick(self, now: float) -> None:
        self.count += 1
        if self.last_t <= 0.0:
            self.last_t = now
            return
        dt = now - self.last_t
        if dt >= 0.5:
            self.fps = self.count / dt
            self.count = 0
            self.last_t = now


class _OverlayManager(QObject):
    def __init__(self, parent_label: QLabel, text: str = "FPS: --"):
        super().__init__(parent_label)
        self.parent_label = parent_label

        ov = QLabel(parent_label)
        ov.setObjectName(f"{parent_label.objectName()}_overlay_fps")
        ov.setText(text)
        ov.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        ov.setStyleSheet(
            """
            QLabel {
                color: #EAEAEA;
                background-color: rgba(0,0,0,140);
                border: 0px;
                padding: 4px 8px;
                font-size: 10pt;
                font-weight: bold;
            }
            """
        )
        ov.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        ov.show()

        self.overlay = ov
        self.parent_label.installEventFilter(self)
        self.reposition()

    def eventFilter(self, obj, event):
        if obj is self.parent_label and event.type() == QEvent.Type.Resize:
            self.reposition()
        return False

    def reposition(self):
        w = self.parent_label.width()
        h = self.parent_label.height()
        if w < 2 or h < 2:
            return
        self.overlay.setWordWrap(True)
        self.overlay.setFixedWidth(w)
        hint_h = self.overlay.sizeHint().height()
        overlay_h = max(26, hint_h)
        self.overlay.setGeometry(0, h - overlay_h, w, overlay_h)

    def set_text(self, text: str):
        try:
            if self.overlay is None:
                return
            self.overlay.setText(text)
        except RuntimeError:
            return


class LivePage(QWidget):
    arm_detected_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        uic.loadUi(ui_path("Admin_LIVE.ui"), self)

        # 키 입력 받기(토글/ROI 미세조정)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ROI 로드
        self._roi_path = Path.cwd() / "homography.json"
        self._global_roi: dict | None = self._load_global_roi()

        # UI refs
        self.lbl_left = self.findChild(QLabel, "lbl_global_cam")
        self.lbl_cam1 = self.findChild(QLabel, "lbl_cam_car1")
        self.lbl_cam2 = self.findChild(QLabel, "lbl_cam_car2")
        self.lbl_arm = self.findChild(QLabel, "lbl_cam_arm")

        self.stack_right: QStackedWidget | None = self.findChild(QStackedWidget, "stack_right_cam")
        self.lbl_right_global = self.findChild(QLabel, "lbl_cam_global_right")

        if self.lbl_left is None or self.lbl_cam1 is None or self.lbl_cam2 is None or self.lbl_arm is None:
            raise RuntimeError("[LIVE] UI QLabel objectName 누락: lbl_global_cam / lbl_cam_car1 / lbl_cam_car2 / lbl_cam_arm")
        if self.stack_right is None or self.lbl_right_global is None:
            raise RuntimeError("[LIVE] UI objectName 누락: stack_right_cam / lbl_cam_global_right")

        # 라벨들이 포커스를 뺏지 않게
        for lb in (self.lbl_left, self.lbl_right_global, self.lbl_cam1, self.lbl_cam2, self.lbl_arm):
            lb.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.lbl_left.setText("GLOBAL VIEW")
        self.lbl_cam1.setText("CAR1 CAM")
        self.lbl_cam2.setText("CAR2 CAM")
        self.lbl_arm.setText("ARM CAM")
        self.lbl_right_global.setText("GLOBAL VIEW")

        # LEFT stack (GLOBAL <-> MOSAIC)
        left_parent = self.lbl_left.parentWidget()
        if left_parent is None or left_parent.layout() is None:
            raise RuntimeError("[LIVE] lbl_left parent/layout missing (check UI)")

        left_layout = left_parent.layout()
        left_idx = left_layout.indexOf(self.lbl_left)
        left_layout.removeWidget(self.lbl_left)

        self.stack_left = QStackedWidget(left_parent)
        self.stack_left.setObjectName("stack_left_view")
        left_layout.insertWidget(left_idx, self.stack_left)

        self.stack_left.addWidget(self.lbl_left)  # page0 = global

        self.mosaic_container = QWidget(self.stack_left)
        self.mosaic_container.setObjectName("mosaic_container")
        mos_layout = QGridLayout(self.mosaic_container)
        mos_layout.setSpacing(10)
        mos_layout.setContentsMargins(0, 0, 0, 0)

        self.stack_left.addWidget(self.mosaic_container)  # page1 = mosaic
        self.stack_left.setCurrentIndex(0)

        self._layout_small_cams = self.findChild(QGridLayout, "layout_small_cams") or self.lbl_cam1.parentWidget().layout()
        self._mos_layout = mos_layout
        self._cams_in_left = False

        # Streams
        self.usb_index = 0
        self.cap_global: cv2.VideoCapture | None = None
        self._cap_reported = False

        self.recv_cam1 = GstReceiver(port=6000, jitter_latency_ms=0)
        self.recv_cam2 = GstReceiver(port=6001, jitter_latency_ms=0)
        self.recv_arm = GstReceiver(port=5002, jitter_latency_ms=0)

        self._latest_cam1: np.ndarray | None = None
        self._latest_cam2: np.ndarray | None = None
        self._latest_arm: np.ndarray | None = None

        # FPS
        self._fps_global = _FpsMeter()
        self._fps_cam1 = _FpsMeter()
        self._fps_cam2 = _FpsMeter()
        self._fps_arm = _FpsMeter()

        self.recv_cam1.frame.connect(self._on_cam1_frame)
        self.recv_cam2.frame.connect(self._on_cam2_frame)
        self.recv_arm.frame.connect(self._on_arm_frame)

        self._mosaic_mode = False

        # Overlays (좌측 글로벌 overlay에는 ROI도 같이 표시)
        self._ov_left = _OverlayManager(self.lbl_left, "FPS: --")
        self._ov_cam1 = _OverlayManager(self.lbl_cam1, "FPS: --")
        self._ov_cam2 = _OverlayManager(self.lbl_cam2, "FPS: --")
        self._ov_arm = _OverlayManager(self.lbl_arm, "FPS: --")
        self._ov_right_global = _OverlayManager(self.lbl_right_global, "FPS: --")

        self._apply_mode_ui()

        # Render timer
        self._streams_started = False
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # -------------------------
    # ROI load/save
    # -------------------------
    def _load_global_roi(self) -> dict | None:
        try:
            if not self._roi_path.exists():
                print("[LIVE] homography.json not found (ROI disabled)")
                return None

            data = json.loads(self._roi_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                print("[LIVE] homography.json invalid (ROI disabled)")
                return None

            roi = None
            if isinstance(data.get("roi"), dict):
                roi = data["roi"]
            elif isinstance(data.get("global_roi"), dict):
                roi = data["global_roi"]

            if not isinstance(roi, dict) or not all(k in roi for k in ("x", "y", "w", "h")):
                print("[LIVE] ROI not found/invalid (ROI disabled)")
                return None

            rx, ry, rw, rh = int(roi["x"]), int(roi["y"]), int(roi["w"]), int(roi["h"])
            if rw <= 0 or rh <= 0:
                print("[LIVE] ROI invalid size (ROI disabled)")
                return None

            print(f"[LIVE] Global ROI loaded: x={rx}, y={ry}, w={rw}, h={rh} (base={ROI_BASE_W}x{ROI_BASE_H})")
            return {"x": rx, "y": ry, "w": rw, "h": rh}
        except Exception as e:
            print(f"[LIVE] ROI load failed (ROI disabled): {e}")
            return None

    def _save_global_roi(self) -> None:
        if self._global_roi is None:
            print("[LIVE] ROI is None (nothing to save)")
            return
        try:
            data = {}
            if self._roi_path.exists():
                raw = self._roi_path.read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else {}
                if not isinstance(data, dict):
                    data = {}

            # 기본은 data["roi"]에 저장(기존 포맷 유지)
            data["roi"] = {
                "x": int(self._global_roi["x"]),
                "y": int(self._global_roi["y"]),
                "w": int(self._global_roi["w"]),
                "h": int(self._global_roi["h"]),
            }
            self._roi_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[LIVE] ROI saved => {self._roi_path} : {data['roi']}")
        except Exception as e:
            print(f"[LIVE] ROI save failed: {e}")

    # -------------------------
    # ROI math
    # -------------------------
    def _roi_fits(self, roi: dict, W: int, H: int) -> bool:
        x, y, w, h = int(roi["x"]), int(roi["y"]), int(roi["w"]), int(roi["h"])
        return (0 <= x < W) and (0 <= y < H) and (x + w <= W) and (y + h <= H) and (w > 0) and (h > 0)

    def _scale_roi(self, roi: dict, W: int, H: int) -> dict:
        sx = W / float(ROI_BASE_W)
        sy = H / float(ROI_BASE_H)
        return {
            "x": int(round(roi["x"] * sx)),
            "y": int(round(roi["y"] * sy)),
            "w": int(round(roi["w"] * sx)),
            "h": int(round(roi["h"] * sy)),
        }

    def _crop_roi(self, frame: np.ndarray, roi: dict) -> np.ndarray:
        H, W = frame.shape[:2]
        x = int(roi["x"])
        y = int(roi["y"])
        w = int(roi["w"])
        h = int(roi["h"])

        x1 = max(0, min(W, x))
        y1 = max(0, min(H, y))
        x2 = max(0, min(W, x + w))
        y2 = max(0, min(H, y + h))
        if x2 <= x1 or y2 <= y1:
            return frame
        return frame[y1:y2, x1:x2]

    def _process_global_for_display(self, frame: np.ndarray) -> np.ndarray:
        if self._global_roi is None:
            return frame

        H, W = frame.shape[:2]
        roi = self._global_roi

        # 1) ROI가 실제 프레임에 그대로 들어맞으면 그대로 사용
        if self._roi_fits(roi, W, H):
            return self._crop_roi(frame, roi)

        # 2) 아니면 base(1920x1080) 기준으로 스케일해서 사용
        roi2 = self._scale_roi(roi, W, H)
        return self._crop_roi(frame, roi2)

    def _draw_border(self, img_bgr: np.ndarray) -> np.ndarray:
        # 크롭 결과 프레임 테두리를 노란색(BGR)으로 표시
        out = img_bgr.copy()
        h, w = out.shape[:2]
        if w >= 2 and h >= 2:
            cv2.rectangle(out, (0, 0), (w - 1, h - 1), (0, 255, 255), 2)  # yellow
        return out

    def _show_global(self, target_label: QLabel, frame: np.ndarray | None) -> None:
        if frame is None:
            target_label.clear()
            target_label.setText("GLOBAL VIEW")
            return

        cropped = self._process_global_for_display(frame)
        cropped = self._draw_border(cropped)  # ✅ 노란 테두리 표시
        show_frame(target_label, cropped, mode="fit")  # ✅ fit 유지(토글해도 직사각형 고정)

    # -------------------------
    # UI helpers
    # -------------------------
    def _move_3cams(self, to_left: bool) -> None:
        if to_left and self._cams_in_left:
            return
        if (not to_left) and (not self._cams_in_left):
            return

        if to_left:
            self._layout_small_cams.removeWidget(self.lbl_cam1)
            self._layout_small_cams.removeWidget(self.lbl_cam2)
            self._layout_small_cams.removeWidget(self.lbl_arm)

            self._mos_layout.addWidget(self.lbl_cam1, 0, 0)
            self._mos_layout.addWidget(self.lbl_cam2, 0, 1)
            self._mos_layout.addWidget(self.lbl_arm, 1, 0, 1, 2)
            self._cams_in_left = True
        else:
            self._mos_layout.removeWidget(self.lbl_cam1)
            self._mos_layout.removeWidget(self.lbl_cam2)
            self._mos_layout.removeWidget(self.lbl_arm)

            self._layout_small_cams.addWidget(self.lbl_cam1, 0, 0)
            self._layout_small_cams.addWidget(self.lbl_cam2, 0, 1)
            self._layout_small_cams.addWidget(self.lbl_arm, 1, 0, 1, 2)
            self._cams_in_left = False

        self.lbl_cam1.updateGeometry()
        self.lbl_cam2.updateGeometry()
        self.lbl_arm.updateGeometry()
        self.mosaic_container.updateGeometry()

        self._ov_cam1.reposition()
        self._ov_cam2.reposition()
        self._ov_arm.reposition()

        self.mosaic_container.repaint()
        self.repaint()

    def _apply_mode_ui(self) -> None:
        self.stack_right.setCurrentIndex(1 if self._mosaic_mode else 0)
        self.stack_left.setCurrentIndex(1 if self._mosaic_mode else 0)

        self._move_3cams(to_left=self._mosaic_mode)

        if self._mosaic_mode:
            self._ov_right_global.overlay.show()
            self._ov_left.overlay.hide()
        else:
            self._ov_right_global.overlay.hide()
            self._ov_left.overlay.show()

        self.stack_left.updateGeometry()
        self.stack_right.updateGeometry()
        self.updateGeometry()
        self.repaint()

    # -------------------------
    # Stream control
    # -------------------------
    def _open_global_cam(self) -> None:
        if self.cap_global and self.cap_global.isOpened():
            return

        cap = cv2.VideoCapture(self.usb_index, cv2.CAP_V4L2)
        if not cap.isOpened():
            print(f"[LIVE] Global CAM open failed: /dev/video{self.usb_index}")
            return

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        self.cap_global = cap

    def _streams_on(self) -> None:
        self._open_global_cam()
        for r in (self.recv_cam1, self.recv_cam2, self.recv_arm):
            if not r.isRunning():
                r.start()

    def _streams_off(self) -> None:
        for r in (self.recv_cam1, self.recv_cam2, self.recv_arm):
            r.stop()
            r.wait(300)
        if self.cap_global:
            self.cap_global.release()
            self.cap_global = None

    # -------------------------
    # Compatibility
    # -------------------------
    def reset_global_layout(self) -> None:
        self._mosaic_mode = False
        self._apply_mode_ui()

    def swap_3cams_with_global(self) -> None:
        print("[LIVE] hotkey 3 received")
        self._mosaic_mode = not self._mosaic_mode
        self._apply_mode_ui()
        print(f"[LIVE] mosaic_mode={'ON' if self._mosaic_mode else 'OFF'}")

    # -------------------------
    # ROI fine tuning hotkeys
    # -------------------------
    def _roi_step(self, ev) -> int:
        step = 1
        mods = ev.modifiers()
        if mods & Qt.KeyboardModifier.AltModifier:
            step = 5
        if mods & Qt.KeyboardModifier.ControlModifier:
            step = 10
        return step

    def keyPressEvent(self, ev):
        # ROI 없으면 로드부터 하게
        if self._global_roi is None:
            if ev.key() in (Qt.Key.Key_R,):
                self._global_roi = self._load_global_roi()
            return super().keyPressEvent(ev)

        key = ev.key()
        step = self._roi_step(ev)
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # save / reload
        if key == Qt.Key.Key_S:
            self._save_global_roi()
            return
        if key == Qt.Key.Key_R:
            self._global_roi = self._load_global_roi()
            return

        # ROI move/resize
        dx = dy = dw = dh = 0

        if not shift:
            # position
            if key == Qt.Key.Key_Left:
                dx = -step
            elif key == Qt.Key.Key_Right:
                dx = +step
            elif key == Qt.Key.Key_Up:
                dy = -step
            elif key == Qt.Key.Key_Down:
                dy = +step
        else:
            # size
            if key == Qt.Key.Key_Left:
                dw = -step
            elif key == Qt.Key.Key_Right:
                dw = +step
            elif key == Qt.Key.Key_Up:
                dh = -step
            elif key == Qt.Key.Key_Down:
                dh = +step

        if dx or dy or dw or dh:
            x = int(self._global_roi["x"]) + dx
            y = int(self._global_roi["y"]) + dy
            w = int(self._global_roi["w"]) + dw
            h = int(self._global_roi["h"]) + dh

            # 최소값 보호
            w = max(10, w)
            h = max(10, h)
            x = max(0, x)
            y = max(0, y)

            self._global_roi.update({"x": x, "y": y, "w": w, "h": h})
            print(f"[LIVE] ROI tweak => x={x}, y={y}, w={w}, h={h} (step={step}, {'SIZE' if shift else 'POS'})")
            return

        return super().keyPressEvent(ev)

    # -------------------------
    # Frame receive
    # -------------------------
    def _on_cam1_frame(self, f: np.ndarray) -> None:
        self._latest_cam1 = f
        self._fps_cam1.tick(time.time())

    def _on_cam2_frame(self, f: np.ndarray) -> None:
        self._latest_cam2 = f
        self._fps_cam2.tick(time.time())

    def _on_arm_frame(self, f: np.ndarray) -> None:
        self._latest_arm = f
        self._fps_arm.tick(time.time())

    # -------------------------
    # Main tick
    # -------------------------
    def _tick(self) -> None:
        now = time.time()

        global_frame: np.ndarray | None = None
        if self.cap_global and self.cap_global.isOpened():
            ok, f = self.cap_global.read()
            if ok:
                global_frame = f
                self._fps_global.tick(now)
                if not self._cap_reported:
                    H, W = f.shape[:2]
                    print(f"[LIVE] Global CAM actual frame size: {W}x{H}")
                    self._cap_reported = True

        cam1 = self._latest_cam1
        cam2 = self._latest_cam2
        arm = self._latest_arm

        # overlay 텍스트 구성(ROI 포함)
        roi_txt = ""
        if self._global_roi is not None:
            r = self._global_roi
            roi_txt = f" | ROI x={r['x']} y={r['y']} w={r['w']} h={r['h']} (S=save R=reload)"

        if not self._mosaic_mode:
            self._show_global(self.lbl_left, global_frame)
            if cam1 is not None:
                show_frame(self.lbl_cam1, cam1, mode="smart")
            if cam2 is not None:
                show_frame(self.lbl_cam2, cam2, mode="smart")
            if arm is not None:
                show_frame(self.lbl_arm, arm, mode="fit")

            self._ov_left.set_text(f"GLOBAL FPS: {self._fps_global.fps:0.1f}{roi_txt}")
            self._ov_cam1.set_text(f"CAR1 FPS: {self._fps_cam1.fps:0.1f}")
            self._ov_cam2.set_text(f"CAR2 FPS: {self._fps_cam2.fps:0.1f}")
            self._ov_arm.set_text(f"ARM  FPS: {self._fps_arm.fps:0.1f}")
        else:
            if cam1 is not None:
                show_frame(self.lbl_cam1, cam1, mode="smart")
            if cam2 is not None:
                show_frame(self.lbl_cam2, cam2, mode="smart")
            if arm is not None:
                show_frame(self.lbl_arm, arm, mode="fit")

            self._show_global(self.lbl_right_global, global_frame)

            self._ov_cam1.set_text(f"CAR1 FPS: {self._fps_cam1.fps:0.1f}")
            self._ov_cam2.set_text(f"CAR2 FPS: {self._fps_cam2.fps:0.1f}")
            self._ov_arm.set_text(f"ARM  FPS: {self._fps_arm.fps:0.1f}")
            self._ov_right_global.set_text(f"GLOBAL FPS: {self._fps_global.fps:0.1f}{roi_txt}")

    # -------------------------
    # Qt events
    # -------------------------
    def showEvent(self, event):
        super().showEvent(event)
        if not self._streams_started:
            self._streams_on()
            self._streams_started = True

        # Live 탭 열릴 때 포커스 확보(ROI 키가 먹게)
        self.setFocus()

    def hideEvent(self, event):
        super().hideEvent(event)

    def closeEvent(self, event):
        self._streams_off()
        super().closeEvent(event)
