# admin_app/main_window.py
from PyQt6 import uic
from PyQt6.QtCore import QTimer, QDateTime, Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtGui import QShortcut, QKeySequence

from .utils.paths import ui_path
from .pages.live_page import LivePage
from .pages.status_page import StatusPage
from .pages.timeline_page import TimelinePage


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)


def _replace_tab(main_window: QWidget, tab_object_name: str, page_widget: QWidget):
    tab = main_window.findChild(QWidget, tab_object_name)
    if tab is None:
        raise RuntimeError(f"탭 위젯을 찾지 못했습니다: objectName='{tab_object_name}'")

    layout = tab.layout()
    if layout is None:
        layout = QVBoxLayout(tab)
        tab.setLayout(layout)

    _clear_layout(layout)
    layout.addWidget(page_widget)


class AdminMainWindow:
    def __init__(self):
        self.window = uic.loadUi(ui_path("Admin_Mainwindow.ui"))

        # pages
        self.live_page = LivePage()
        self.status_page = StatusPage()
        self.timeline_page = TimelinePage()

        _replace_tab(self.window, "tab_live", self.live_page)
        _replace_tab(self.window, "tab_status", self.status_page)
        _replace_tab(self.window, "tab_timeline", self.timeline_page)

        # 상태값(허브에서 관리)
        self._arm_detected = False

        self._setup_clock()
        self.set_conn_text("CONN: TCP=OFF  CAM=OFF  (READ-ONLY)")
        self._setup_shortcuts()

        # ✅ LIVE에서 YOLO 감지되면 허브로 들어오게 연결
        self.live_page.arm_detected_changed.connect(lambda v: self.set_arm_detected(bool(v), reason="live_yolo"))

        # 초기 UI 반영(미인식)
        self.set_arm_detected(False, reason="init")

    # =========================
    # Public API (TCP 수신부가 여기 호출)
    # =========================
    def set_arm_detected(self, detected: bool, reason: str = ""):
        """
        CHARM이 '충전구 인식' 이벤트를 보내면,
        main_window에서 한 번만 받아서 LIVE/STATUS 둘 다 갱신한다.
        """
        self._arm_detected = bool(detected)

        # LIVE: 버튼 enable/disable + 라벨 텍스트
        if hasattr(self.live_page, "set_arm_detection"):
            self.live_page.set_arm_detection(self._arm_detected)

        # STATUS: 표시만
        if hasattr(self.status_page, "set_arm_detection"):
            self.status_page.set_arm_detection(self._arm_detected)

        if reason:
            print(f"[MAIN] set_arm_detected={self._arm_detected} ({reason})")

    # =========================
    # Shortcuts
    # =========================
    def _setup_shortcuts(self):
        # 단축키 객체가 GC로 사라지지 않게 보관
        self._shortcuts = []

        def _bind_live(keyseq: str, handler_name: str, *, label: str) -> None:
            sc = QShortcut(QKeySequence(keyseq), self.window)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.setAutoRepeat(False)

            fn = getattr(self.live_page, handler_name, None)
            if callable(fn):
                sc.activated.connect(fn)
            else:
                print("[WARN] LivePage." + str(handler_name) + " not implemented; hotkey '" + str(label) + "' disabled")

            self._shortcuts.append(sc)

        # '3' : 3캠/글로벌 스왑
        _bind_live("3", "swap_3cams_with_global", label="3")

        # 'g' : 원복(글로벌 레이아웃)
        _bind_live("g", "reset_global_layout", label="g")

        # ✅ (테스트용) 'd' : 인식/미인식 토글 (통신 붙이기 전 디자인 검증)
        scd = QShortcut(QKeySequence("d"), self.window)
        scd.setContext(Qt.ShortcutContext.ApplicationShortcut)
        scd.setAutoRepeat(False)
        scd.activated.connect(self._toggle_arm_detected_dummy)
        self._shortcuts.append(scd)

        # ✅ (테스트용) 'D' : 강제로 DETECTED
        scD = QShortcut(QKeySequence("Shift+d"), self.window)
        scD.setContext(Qt.ShortcutContext.ApplicationShortcut)
        scD.setAutoRepeat(False)
        scD.activated.connect(lambda: self.set_arm_detected(True, reason="hotkey"))
        self._shortcuts.append(scD)

        # ✅ (테스트용) 'u' : 강제로 UNDETECTED
        scu = QShortcut(QKeySequence("u"), self.window)
        scu.setContext(Qt.ShortcutContext.ApplicationShortcut)
        scu.setAutoRepeat(False)
        scu.activated.connect(lambda: self.set_arm_detected(False, reason="hotkey"))
        self._shortcuts.append(scu)


    def _toggle_arm_detected_dummy(self):
        self.set_arm_detected(not self._arm_detected, reason="toggle")

    # =========================
    # Clock
    # =========================
    def _setup_clock(self):
        lbl_clock = self.window.findChild(QLabel, "lbl_clock")
        if lbl_clock is None:
            return

        self._clock_timer = QTimer(self.window)
        self._clock_timer.setInterval(1000)

        def tick():
            lbl_clock.setText(QDateTime.currentDateTime().toString("HH:mm:ss"))

        self._clock_timer.timeout.connect(tick)
        tick()
        self._clock_timer.start()

    # =========================
    # Header text
    # =========================
    def set_conn_text(self, text: str):
        lbl_conn = self.window.findChild(QLabel, "lbl_conn")
        if lbl_conn is not None:
            lbl_conn.setText(text)

    def show(self):
        self.window.show()
