# ==========================
# file: user_ui/main_window.py
# ==========================
import os
import math
import random
import datetime as dt
import ast

import rclpy

from PyQt6.QtCore import QTimer, pyqtSignal, Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import QMainWindow, QStackedWidget, QLabel

from user_ui.pages import BootPage, DestinationPage, MovingPage, PaymentPage, W, H
from camera.ros_iface import RosInterface


VIA_RULES = [
    dict(name="ZONE_A", x_min=0.450, x_max=0.950, via=(0.780, -0.015)),
    dict(name="ZONE_B", x_min=0.951, x_max=1.400, via=(1.230, -0.015)),
    dict(name="ZONE_C", x_min=1.401, x_max=1.720, via=(1.610, -0.015)),
]

WAYPOINT_TOL = 0.10
WAYPOINT_CHECK_MS = 100


def _load_map_yaml(yaml_path: str) -> dict:
    if not os.path.exists(yaml_path):
        raise FileNotFoundError(f"map yaml not found: {yaml_path}")

    data = {}
    with open(yaml_path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip()

    if "image" not in data:
        raise ValueError(f"'image' missing in yaml: {yaml_path}")
    if "resolution" not in data:
        raise ValueError(f"'resolution' missing in yaml: {yaml_path}")
    if "origin" not in data:
        raise ValueError(f"'origin' missing in yaml: {yaml_path}")

    data["resolution"] = float(data["resolution"])

    origin = ast.literal_eval(data["origin"])
    if not (isinstance(origin, (list, tuple)) and len(origin) == 3):
        raise ValueError("origin must be [x, y, yaw]")
    data["origin"] = [float(origin[0]), float(origin[1]), float(origin[2])]
    return data


class MainWindow(QMainWindow):
    docking_status_sig = pyqtSignal(str)

    def __init__(self, project_root: str):
        super().__init__()
        self.project_root = os.path.abspath(project_root)

        self.setWindowTitle("AutoServe")
        self.setFixedSize(W, H)

        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)

        self.p1 = BootPage()
        self.p2 = DestinationPage()
        self.p3 = MovingPage()
        self.p4 = PaymentPage()

        for p in (self.p1, self.p2, self.p3, self.p4):
            self.stack.addWidget(p)

        self.lbl_battery = QLabel("무인 택시 배터리 잔량 : --%", self)
        LABEL_W = 420
        LABEL_H = 44
        MARGIN_R = 20
        MARGIN_T = 12
        self.lbl_battery.setGeometry(W - LABEL_W - MARGIN_R, MARGIN_T, LABEL_W, LABEL_H)
        self.lbl_battery.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_battery.setFont(QFont("", 18, QFont.Weight.Bold))
        self.lbl_battery.setStyleSheet("background: transparent; color: white;")
        self._last_battery_percent = None

        self.back_to_boot_timer = QTimer(self)
        self.back_to_boot_timer.setSingleShot(True)
        self.back_to_boot_timer.timeout.connect(self.back_to_boot)

        # MAP load
        yaml_path = os.path.join(self.project_root, "pix_map.yaml")
        map_cfg = _load_map_yaml(yaml_path)

        img_rel = map_cfg["image"]
        img_path = img_rel if os.path.isabs(img_rel) else os.path.join(self.project_root, img_rel)
        if not os.path.exists(img_path):
            raise FileNotFoundError(f"map image not found: {img_path} (from {yaml_path})")

        self.map_resolution = float(map_cfg["resolution"])
        self.map_origin_x, self.map_origin_y, self.map_origin_yaw = map_cfg["origin"]

        self.map_pix = QPixmap(img_path)
        if self.map_pix.isNull():
            raise RuntimeError(f"failed to load map image: {img_path}")

        self.map_img_w = self.map_pix.width()
        self.map_img_h = self.map_pix.height()

        self.p2.set_map_pixmap(self.map_pix)

        def pixel_to_map(u: float, v: float) -> tuple[float, float]:
            x_local = float(u) * self.map_resolution
            y_local = float((self.map_img_h - 1) - float(v)) * self.map_resolution

            yaw = float(self.map_origin_yaw)
            c = math.cos(yaw)
            s = math.sin(yaw)
            x_rot = c * x_local - s * y_local
            y_rot = s * x_local + c * y_local

            x_map = float(self.map_origin_x) + x_rot
            y_map = float(self.map_origin_y) + y_rot
            return x_map, y_map

        self.p2.set_pixel_to_world_fn(pixel_to_map)

        # ROS node
        self.node = RosInterface()
        self._last_printed_status = None
        self.docking_status_sig.connect(self.on_docking_status)

        # ✅ waypoint sequence state
        self._goal_queue: list[tuple[float, float]] = []
        self._active_goal: tuple[float, float] | None = None
        self._final_yaw: float = 0.0  # ✅ 최종 goal에만 적용할 yaw

        self.wp_timer = QTimer(self)
        self.wp_timer.setInterval(WAYPOINT_CHECK_MS)
        self.wp_timer.timeout.connect(self._waypoint_tick)
        self.wp_timer.start()

        # page connect
        self.p1.go_next.connect(lambda: self.stack.setCurrentWidget(self.p2))
        self.p2.confirm_clicked.connect(self.on_confirm_publish_and_go)
        self.p3.payment.connect(self.on_payment_generate_and_go)

        self.ros_timer = QTimer(self)
        self.ros_timer.setInterval(10)
        self.ros_timer.timeout.connect(self.spin_ros)
        self.ros_timer.start()

        self.batt_timer = QTimer(self)
        self.batt_timer.setInterval(500)
        self.batt_timer.timeout.connect(self.update_battery_label)
        self.batt_timer.start()

        print("[UI] map-only click → /click_goal enabled.")
        for r in VIA_RULES:
            print(f"[RULE] {r['name']}: x in [{r['x_min']}, {r['x_max']}] -> via={r['via']}  tol={WAYPOINT_TOL}m")

    def spin_ros(self):
        try:
            if rclpy.ok():
                rclpy.spin_once(self.node, timeout_sec=0.0)
        except Exception:
            pass

        status = self.node.last_docking_status
        if status and status != self._last_printed_status:
            self._last_printed_status = status
            self.docking_status_sig.emit(status)

    def update_battery_label(self):
        pct = self.node.battery_percent
        if pct is None or pct == self._last_battery_percent:
            return

        self._last_battery_percent = pct
        self.lbl_battery.setText(f"무인 택시 배터리 잔량 : {pct}%")

        if pct <= 10:
            self.lbl_battery.setStyleSheet("background: transparent; color: #ff3333;")
        elif pct <= 30:
            self.lbl_battery.setStyleSheet("background: transparent; color: #ffd000;")
        else:
            self.lbl_battery.setStyleSheet("background: transparent; color: white;")

    def on_docking_status(self, status: str):
        if status == "DOCKING_COMPLETE":
            print("✅ [DOCKING] 완료")
            if self.stack.currentWidget() is self.p3:
                self.p3.set_arrived()
        else:
            print(f"[DOCKING_STATUS] {status}")

    # =====================
    # ✅ waypoint publish sequence
    # =====================
    def _start_goal_sequence(self, goals: list[tuple[float, float]], final_yaw: float):
        self._goal_queue = list(goals)
        self._active_goal = None
        self._final_yaw = float(final_yaw)
        self._publish_next_from_queue()

    def _publish_next_from_queue(self):
        if not self._goal_queue:
            self._active_goal = None
            return

        x, y = self._goal_queue.pop(0)
        self._active_goal = (x, y)

        is_final = (len(self._goal_queue) == 0)

        if is_final:
            self.node.publish_goal(x, y, yaw=self._final_yaw, is_final=True)
            print(f"[SEQ] publish FINAL=({x:.3f},{y:.3f}) yaw={self._final_yaw:.3f}")
        else:
            # ✅ waypoint는 yaw 적용 금지
            self.node.publish_goal(x, y, yaw=0.0, is_final=False)
            print(f"[SEQ] publish waypoint=({x:.3f},{y:.3f}) -> next queued {len(self._goal_queue)}")

    def _waypoint_tick(self):
        if self._active_goal is None:
            return
        if not self._goal_queue:
            return  # final은 waypoint tick로 다음 publish 안 함

        dist = self.node.distance_to(self._active_goal[0], self._active_goal[1])
        if dist is None:
            return

        if dist <= float(WAYPOINT_TOL):
            print(f"[SEQ] reached waypoint within {WAYPOINT_TOL}m (dist={dist:.3f}) -> publish next")
            self._publish_next_from_queue()

    def _build_sequence_for_goal(self, x_goal: float, y_goal: float) -> tuple[str, list[tuple[float, float]]]:
        xg = float(x_goal)
        yg = float(y_goal)

        for r in VIA_RULES:
            x_min = float(r["x_min"])
            x_max = float(r["x_max"])
            if x_min <= xg <= x_max:
                vx, vy = r["via"]
                seq = [(float(vx), float(vy)), (xg, yg)]
                return r.get("name", "VIA"), seq

        return "DIRECT", [(xg, yg)]

    def on_confirm_publish_and_go(self):
        if self.p2.selected_world is None:
            print("[CONFIRM] blocked: selected_world is None")
            return

        self.node.last_docking_status = None
        self._last_printed_status = None

        x_goal, y_goal = self.p2.selected_world
        final_yaw = float(getattr(self.p2, "selected_yaw", 0.0))  # ✅ 오른쪽클릭 yaw

        rule_name, goals = self._build_sequence_for_goal(x_goal, y_goal)
        print(f"[CONFIRM] rule={rule_name} goal=({x_goal:.3f},{y_goal:.3f}) -> seq={goals}")

        self._start_goal_sequence(goals, final_yaw=final_yaw)

        self.stack.setCurrentWidget(self.p3)
        self.p3.start()

    def on_payment_generate_and_go(self):
        self.back_to_boot_timer.stop()

        code = f"{random.randint(0, 99999999):08d}"
        now_seoul = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        self.p4.set_payment_info(code, now_seoul)

        self.stack.setCurrentWidget(self.p4)
        self.back_to_boot_timer.start(10000)

    def back_to_boot(self):
        self.p2.reset_selection()
        self.p3.start()
        self.node.last_docking_status = None
        self._last_printed_status = None

        self._goal_queue = []
        self._active_goal = None
        self._final_yaw = 0.0

        self.stack.setCurrentWidget(self.p1)

    def closeEvent(self, e):
        try:
            self.back_to_boot_timer.stop()
            self.ros_timer.stop()
            self.batt_timer.stop()
            self.wp_timer.stop()

            if self.node:
                self.node.destroy_node()

            if rclpy.ok():
                rclpy.shutdown()
        except Exception:
            pass

        e.accept()
