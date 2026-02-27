import sys
import logging

# 🔥 YOLO/ultralytics 로그 차단
logging.getLogger("ultralytics").setLevel(logging.ERROR)

from PyQt6.QtWidgets import QApplication
from .main_window import AdminMainWindow

def main():
    app = QApplication(sys.argv)
    win = AdminMainWindow()
    win.show()
    sys.exit(app.exec())

