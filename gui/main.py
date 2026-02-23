#!/usr/bin/env python3
import os
import sys

import rclpy
from PyQt6.QtWidgets import QApplication

# ✅ 프로젝트 루트 절대경로
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from user_ui.main_window import MainWindow
from user_ui.style import apply_autoserve_styles


def main():
    # ✅ ROS init (단 1회)
    if not rclpy.ok():
        rclpy.init(args=None)

    app = QApplication(sys.argv)
    apply_autoserve_styles(app)

    w = MainWindow(project_root=PROJECT_ROOT)
    w.show()

    try:
        sys.exit(app.exec())
    finally:
        # closeEvent에서도 정리하지만, 안전하게 한 번 더
        try:
            if rclpy.ok():
                rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
