# admin_app/pages/live_render.py
from __future__ import annotations

import cv2
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy


def _init_label_once(label: QLabel) -> None:
    if getattr(label, "_live_inited", False):
        return

    label.setScaledContents(False)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    label.setMinimumSize(1, 1)

    label._live_inited = True


def show_frame(
    label: QLabel | None,
    frame_bgr: np.ndarray | None,
    *,
    mode: str = "fit",  # "fit" | "fill" | "stretch" | "smart"
) -> None:
    """
    mode
      - "fit": KeepAspectRatio (안 잘림, 공백 생길 수 있음)
      - "fill": KeepAspectRatioByExpanding (공백 없음, 잘릴 수 있음)
      - "stretch": IgnoreAspectRatio (공백 없음, 안 잘림, 대신 왜곡 가능)
      - "smart": 화면/라벨 비율 차이가 크면 fit, 작으면 fill
    """
    if label is None or frame_bgr is None:
        return

    _init_label_once(label)

    lw = label.width()
    lh = label.height()
    if lw < 2 or lh < 2:
        return

    h, w = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

    # 안전성: numpy 버퍼 수명 이슈 방지
    qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
    pix = QPixmap.fromImage(qimg)

    if mode == "smart":
        frame_ratio = (w / h) if h > 0 else 1.0
        label_ratio = (lw / lh) if lh > 0 else 1.0
        ratio_delta = abs(frame_ratio - label_ratio) / max(frame_ratio, label_ratio, 1e-6)
        mode = "fit" if ratio_delta > 0.18 else "fill"

    if mode == "stretch":
        pix_out = pix.scaled(
            lw,
            lh,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    elif mode == "fill":
        pix_out = pix.scaled(
            lw,
            lh,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
    else:
        pix_out = pix.scaled(
            lw,
            lh,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    label.setPixmap(pix_out)


def clear_label(label: QLabel | None) -> None:
    if label is None:
        return
    label.clear()
