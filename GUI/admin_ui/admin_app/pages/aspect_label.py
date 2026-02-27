# admin_app/pages/aspect_label.py
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import QLabel, QSizePolicy


class AspectRatioLabel(QLabel):
    """
    라벨 영역 자체를 특정 비율로 유지합니다.
    - 비율 유지(왜곡 X)
    - 레이아웃이 커지면 그 안에서 최대한 크게(여백 최소화)
    """
    def __init__(self, ratio_w: int = 16, ratio_h: int = 9, parent=None):
        super().__init__(parent)
        self.ratio_w = max(1, int(ratio_w))
        self.ratio_h = max(1, int(ratio_h))

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)

        # 레이아웃에 끌려다니지 않게
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(1, 1)

    def set_ratio(self, w: int, h: int) -> None:
        self.ratio_w = max(1, int(w))
        self.ratio_h = max(1, int(h))
        self.updateGeometry()
        self.update()

    def heightForWidth(self, w: int) -> int:
        # h = w * (ratio_h/ratio_w)
        return int(w * self.ratio_h / self.ratio_w)

    def hasHeightForWidth(self) -> bool:
        return True

    def sizeHint(self) -> QSize:
        # 적당한 기본값
        return QSize(640, self.heightForWidth(640))

    def minimumSizeHint(self) -> QSize:
        return QSize(160, self.heightForWidth(160))
