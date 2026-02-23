# ==========================
# file: user_ui/pages.py
# ==========================
import datetime as dt
import math

from PyQt6.QtCore import Qt, pyqtSignal, QPointF
from PyQt6.QtGui import QPixmap, QFont, QPainter, QPen, QColor, QPolygonF
from PyQt6.QtWidgets import QWidget, QLabel, QPushButton

W, H = 1440, 900


class ClickableLabel(QLabel):
    clicked = pyqtSignal(int, int)
    right_clicked = pyqtSignal(int, int)  # ✅ 우클릭

    def mousePressEvent(self, event):
        p = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(int(p.x()), int(p.y()))
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit(int(p.x()), int(p.y()))
            event.accept()
        else:
            super().mousePressEvent(event)


class BootPage(QWidget):
    go_next = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page_boot")

        label = QLabel("AutoServe를 이용해주셔서 감사합니다.\n아무 곳이나 클릭하세요", self)
        label.setGeometry(0, 350, W, 200)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(QFont("", 28, QFont.Weight.Bold))

    def mousePressEvent(self, e):
        self.go_next.emit()
        e.accept()


class DestinationPage(QWidget):
    confirm_clicked = pyqtSignal()

    # 링(가운데 뚫린 원)
    MARKER_RADIUS = 6
    MARKER_PEN_WIDTH = 3

    # pinky 박스(시각용)
    PINKY_BOX_W = 120
    PINKY_BOX_H = 66

    # 핑키 시작 좌표 (u,v)
    FIXED_PINKY_IMG_U = 25.2
    FIXED_PINKY_IMG_V = 45.5

    # ✅ 방향 화살표 설정(시각용)
    ARROW_LEN = 40
    ARROW_PEN_WIDTH = 4

    def __init__(self):
        super().__init__()
        self.setObjectName("page_destination")

        self.selected_world = None      # (X, Y)
        self.selected_img_px = None     # (u, v) in map image pixel coords
        self.selected_yaw = None        # ✅ yaw(rad) or None

        title = QLabel("목적지를 선택해주십시오", self)
        title.setGeometry(0, 30, W, 50)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("", 18, QFont.Weight.Bold))

        self.btn_confirm = QPushButton("확인", self)
        self.btn_confirm.setObjectName("btn_confirm")
        self.btn_confirm.setGeometry(1120, 75, 180, 45)
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.clicked.connect(self.confirm_clicked.emit)

        self.map_view = ClickableLabel("", self)
        self.map_view.setObjectName("map_view")
        self.map_view.setGeometry(140, 130, 1160, 690)
        self.map_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_view.clicked.connect(self.on_click)
        self.map_view.right_clicked.connect(self.on_right_click)  # ✅ 추가

        hint = QLabel("※ 좌클릭: 목적지 / 우클릭: 바라볼 방향 지정", self)
        hint.setGeometry(140, 835, 1160, 24)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setFont(QFont("", 10))

        # KeepAspectRatio 여백 계산용
        self._disp = {}

        # 외부 주입 함수: (u,v) -> (x,y)
        self.pixel_to_world_fn = None

        # 지도 pixmap 원본
        self.map_pix = None
        self.map_img_w = 0
        self.map_img_h = 0

    def set_pixel_to_world_fn(self, fn):
        self.pixel_to_world_fn = fn

    def set_map_pixmap(self, pix: QPixmap):
        self.map_pix = pix
        self.map_img_w = int(pix.width())
        self.map_img_h = int(pix.height())
        self._render()

    def reset_selection(self):
        self.selected_world = None
        self.selected_img_px = None
        self.selected_yaw = None
        self.btn_confirm.setEnabled(False)
        self._render()

    def _render(self):
        if self.map_pix is None or self.map_pix.isNull():
            self.map_view.setText("MAP NOT LOADED")
            return

        lw, lh = self.map_view.width(), self.map_view.height()
        scaled = self.map_pix.scaled(
            lw, lh,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

        # 라벨에서 scaled가 가운데 배치될 때 생기는 여백
        x_off = (lw - scaled.width()) // 2
        y_off = (lh - scaled.height()) // 2

        # 클릭 좌표 -> 이미지 좌표 변환에 필요
        self._disp = dict(
            x_off=x_off, y_off=y_off,
            disp_w=scaled.width(), disp_h=scaled.height(),
            img_w=self.map_img_w, img_h=self.map_img_h
        )

        out = QPixmap(scaled)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        denom_w = max(1.0, float(self.map_img_w - 1))
        denom_h = max(1.0, float(self.map_img_h - 1))

        # =========================================================
        # (A) 고정 pinky 박스: img=(25.2,45.5) 위치에 항상 표시
        # =========================================================
        fu = float(self.FIXED_PINKY_IMG_U)
        fv = float(self.FIXED_PINKY_IMG_V)

        fsx = (fu / denom_w) * float(scaled.width())
        fsy = (fv / denom_h) * float(scaled.height())

        bw = int(self.PINKY_BOX_W)
        bh = int(self.PINKY_BOX_H)

        bx = int(fsx - bw * 0.5)
        by = int(fsy - bh * 0.5)

        bx = max(0, min(bx, int(scaled.width()) - bw))
        by = max(0, min(by, int(scaled.height()) - bh))

        painter.setPen(QPen(QColor(255, 0, 0), 4))
        painter.setBrush(QColor(0, 0, 0, 220))
        painter.drawRect(bx, by, bw, bh)

        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setFont(QFont("", 16, QFont.Weight.Bold))
        painter.drawText(bx, by, bw, bh, Qt.AlignmentFlag.AlignCenter, "pinky")

        # =========================================================
        # (B) 기존 기능: 클릭 링 표시 + (추가) 방향 화살표 표시
        # =========================================================
        if self.selected_img_px is not None:
            u, v = self.selected_img_px

            sx = (float(u) / denom_w) * float(scaled.width())
            sy = (float(v) / denom_h) * float(scaled.height())

            pen = QPen(QColor(255, 0, 0))
            pen.setWidth(self.MARKER_PEN_WIDTH)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            r = self.MARKER_RADIUS
            painter.drawEllipse(int(sx - r), int(sy - r), int(2 * r), int(2 * r))

            # ✅ yaw가 지정되었으면, 목표점에서 화살표로 표시
            if self.selected_yaw is not None:
                yaw = float(self.selected_yaw)

                ex = sx + math.cos(yaw) * float(self.ARROW_LEN)
                ey = sy - math.sin(yaw) * float(self.ARROW_LEN)  # 화면 y축은 아래로 증가

                pen2 = QPen(QColor(255, 0, 0))
                pen2.setWidth(self.ARROW_PEN_WIDTH)
                painter.setPen(pen2)
                painter.setBrush(Qt.BrushStyle.NoBrush)

                # 본선
                painter.drawLine(int(sx), int(sy), int(ex), int(ey))

                # ✅ 화살촉(삼각형) - None append 제거/수정 완료
                head_len = 12.0
                head_ang = math.radians(25.0)
                a1 = yaw + math.pi - head_ang
                a2 = yaw + math.pi + head_ang

                hx1 = ex + math.cos(a1) * head_len
                hy1 = ey - math.sin(a1) * head_len
                hx2 = ex + math.cos(a2) * head_len
                hy2 = ey - math.sin(a2) * head_len

                poly = QPolygonF()
                poly.append(QPointF(ex, ey))
                poly.append(QPointF(hx1, hy1))
                poly.append(QPointF(hx2, hy2))
                painter.drawPolygon(poly)

        painter.end()

        self.map_view.setPixmap(out)
        self.map_view.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _label_to_img_uv(self, lx: int, ly: int):
        """
        라벨 좌표(lx,ly) -> 이미지 픽셀(u,v)
        KeepAspectRatio 여백 고려.
        """
        if self.map_pix is None or self.map_pix.isNull() or not self._disp:
            return None

        d = self._disp
        in_img = (
            d["x_off"] <= lx < d["x_off"] + d["disp_w"]
            and d["y_off"] <= ly < d["y_off"] + d["disp_h"]
        )
        if not in_img:
            return None

        sx = float(lx - d["x_off"])
        sy = float(ly - d["y_off"])

        img_w = float(d["img_w"])
        img_h = float(d["img_h"])
        disp_w = max(1.0, float(d["disp_w"]))
        disp_h = max(1.0, float(d["disp_h"]))

        u = (sx / disp_w) * max(0.0, img_w - 1.0)
        v = (sy / disp_h) * max(0.0, img_h - 1.0)

        u = max(0.0, min(u, img_w - 1.0))
        v = max(0.0, min(v, img_h - 1.0))
        return (u, v)

    def on_click(self, lx: int, ly: int):
        if self.map_pix is None or self.map_pix.isNull() or not self._disp:
            print("[CLICK-IGNORE] map not ready")
            return
        if self.pixel_to_world_fn is None:
            print("[CLICK-IGNORE] pixel_to_world_fn is None")
            return

        uv = self._label_to_img_uv(lx, ly)
        if uv is None:
            print(f"[CLICK-IGNORE] outside image area: lx,ly=({lx},{ly})")
            return

        u, v = uv

        # 이미지 픽셀 -> world 변환
        try:
            X, Y = self.pixel_to_world_fn(float(u), float(v))
        except Exception as e:
            print(f"[CLICK-ERROR] pixel_to_world failed: {e}")
            self.selected_img_px = None
            self.selected_world = None
            self.selected_yaw = None
            self.btn_confirm.setEnabled(False)
            self._render()
            return

        self.selected_img_px = (u, v)
        self.selected_world = (X, Y)
        self.btn_confirm.setEnabled(True)

        # 위치를 새로 찍으면 방향은 초기화(원하시면 유지로 변경 가능)
        self.selected_yaw = None

        self._render()
        print(f"[CLICK] stored (no publish) img=({u:.1f},{v:.1f}) map=({X:.3f},{Y:.3f})")

    def on_right_click(self, lx: int, ly: int):
        """
        우클릭으로 '바라볼 방향(yaw)' 지정
        - 목표점(좌클릭) 없으면 무시
        - 목표점(world) -> 우클릭점(world) 벡터로 yaw 계산(atan2)
        """
        if self.selected_world is None or self.selected_img_px is None:
            print("[RIGHT-CLICK] ignored: goal not selected yet")
            return
        if self.pixel_to_world_fn is None:
            print("[RIGHT-CLICK] ignored: pixel_to_world_fn is None")
            return

        uv = self._label_to_img_uv(lx, ly)
        if uv is None:
            print(f"[RIGHT-CLICK] ignored: outside image area: lx,ly=({lx},{ly})")
            return

        u, v = uv

        try:
            X2, Y2 = self.pixel_to_world_fn(float(u), float(v))
        except Exception as e:
            print(f"[RIGHT-CLICK] pixel_to_world failed: {e}")
            return

        X1, Y1 = self.selected_world
        dx = float(X2) - float(X1)
        dy = float(Y2) - float(Y1)

        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            print("[RIGHT-CLICK] ignored: too close to goal point")
            return

        yaw = math.atan2(dy, dx)  # map frame yaw(rad)
        self.selected_yaw = float(yaw)

        self._render()
        print(f"[YAW] set yaw={yaw:.3f} rad (from goal to right-click point)")


class MovingPage(QWidget):
    payment = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page_moving")

        self.title = QLabel("이동 중입니다...", self)
        self.title.setGeometry(0, 200, W, 80)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setFont(QFont("", 28, QFont.Weight.Bold))

        self.sub = QLabel("목적지로 이동 중입니다. 잠시만 기다려주세요.", self)
        self.sub.setGeometry(0, 310, W, 40)
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.setFont(QFont("", 14))

        self.btn_pay = QPushButton("결제하기", self)
        self.btn_pay.setObjectName("btn_pay")
        self.btn_pay.setGeometry((W - 220) // 2, 420, 220, 60)
        self.btn_pay.setEnabled(False)
        self.btn_pay.clicked.connect(self.payment.emit)

    def start(self):
        self.title.setText("이동 중입니다...")
        self.sub.setText("목적지로 이동 중입니다. 잠시만 기다려주세요.")
        self.btn_pay.setEnabled(False)

    def set_arrived(self):
        self.title.setText("도착했습니다!")
        self.sub.setText("결제를 진행해주세요.")
        self.btn_pay.setEnabled(True)


class PaymentPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("page_payment")

        self.title = QLabel("결제 완료", self)
        self.title.setGeometry(0, 180, W, 70)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setFont(QFont("", 30, QFont.Weight.Bold))

        self.lbl_code = QLabel("승인코드: --------", self)
        self.lbl_code.setGeometry(0, 300, W, 50)
        self.lbl_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_code.setFont(QFont("", 24, QFont.Weight.Bold))

        self.lbl_time = QLabel("시간: ----.--.-- --:--:--", self)
        self.lbl_time.setGeometry(0, 370, W, 40)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_time.setFont(QFont("", 16))

        self.hint = QLabel("10초 후 첫 화면으로 돌아갑니다.", self)
        self.hint.setGeometry(0, 470, W, 30)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setFont(QFont("", 12))

    def set_payment_info(self, code: str, now_seoul: dt.datetime):
        self.lbl_code.setText(f"승인코드: {code}")
        self.lbl_time.setText(f"시간: {now_seoul.strftime('%Y-%m-%d %H:%M:%S')}")
