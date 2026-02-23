from PyQt6.QtWidgets import QApplication


def apply_autoserve_styles(app: QApplication):
    app.setStyleSheet("""
        * { background-color: #000000; color: #FFFFFF; }

        QLabel { color: #FFFFFF; background-color: transparent; }

        QPushButton {
            color: #FFFFFF;
            background-color: #2D2D2D;
            border: 1px solid #444444;
            border-radius: 12px;
            font-size: 16pt;
            font-weight: bold;
        }
        QPushButton:hover { background-color: #3A3A3A; }
        QPushButton:pressed { background-color: #1F1F1F; }
        QPushButton:disabled { color: #777777; background-color: #1F1F1F; border: 1px solid #2A2A2A; }

        QPushButton#btn_confirm {
            background-color: #3A86FF;
            border: none;
        }

        QLabel#map_view {
            background-color: #000000;
            border: 2px solid #333333;
            border-radius: 12px;
        }
    """)

