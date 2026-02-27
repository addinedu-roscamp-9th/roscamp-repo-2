# admin_app/pages/timeline_page.py

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from PyQt6 import uic
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QComboBox,
    QLineEdit,
    QPushButton,
    QLabel,
    QTextEdit,
    QHeaderView,
    QAbstractItemView,
)

from ..utils.paths import ui_path

# ===== FastAPI endpoints (서버 PC 기준) =====
FASTAPI_BASE = "http://192.168.1.8:8000"

# ✅ 서버에 실제로 존재하는 엔드포인트
API_RECENT = f"{FASTAPI_BASE}/events/recent"

AUTO_REFRESH_MS = 1200
RECENT_LIMIT = 200
MAX_GUI_ROWS = 800
ALLOW_LEVELS = {"INFO", "WARN", "ERROR"}  # DEBUG는 GUI에서 배제
ROW_HEIGHT = 28


def _http_get_json(url: str, timeout_s: float = 1.2) -> Optional[dict]:
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


@dataclass
class EventRow:
    id: int
    created_at: str
    src: str
    level: str
    event: str
    detail: str


class TimelinePage(QWidget):
    def __init__(self):
        super().__init__()
        uic.loadUi(ui_path("Admin_TIMELINE.ui"), self)

        # ===== UI 핸들 (Admin_TIMELINE.ui 기준 이름) =====
        self.table: QTableWidget | None = self.findChild(QTableWidget, "table_timeline")
        self.cb_src: QComboBox | None = self.findChild(QComboBox, "cb_source")
        self.cb_level: QComboBox | None = self.findChild(QComboBox, "cb_level")
        self.edit_search: QLineEdit | None = self.findChild(QLineEdit, "edit_search")
        self.btn_apply: QPushButton | None = self.findChild(QPushButton, "btn_apply")
        self.btn_clear: QPushButton | None = self.findChild(QPushButton, "btn_clear")

        self.lbl_time: QLabel | None = self.findChild(QLabel, "lbl_d_time_val")
        self.lbl_src: QLabel | None = self.findChild(QLabel, "lbl_d_src_val")
        self.lbl_level: QLabel | None = self.findChild(QLabel, "lbl_d_level_val")
        self.lbl_event: QLabel | None = self.findChild(QLabel, "lbl_d_event_val")
        self.text_detail: QTextEdit | None = self.findChild(QTextEdit, "text_detail")

        self._last_top_id: int = 0  # 새 이벤트 없으면 렌더 스킵

        if self.table is None:
            raise RuntimeError("[TIMELINE] table_timeline(QTableWidget)을 찾지 못했습니다.")
        if self.text_detail is None:
            raise RuntimeError("[TIMELINE] text_detail(QTextEdit)을 찾지 못했습니다.")

        self.text_detail.setReadOnly(True)

        # ===== Table 세팅 =====
        self._setup_table()
        self._apply_dark_lock_style()

        # ===== State =====
        self.events: list[EventRow] = []
        self.auto_mode = True
        self._baseline = ("ALL", "ALL LEVEL", "")

        # ===== Signals =====
        self.table.cellClicked.connect(self._on_row_clicked)
        if self.btn_apply:
            self.btn_apply.clicked.connect(self._on_apply)
        if self.btn_clear:
            self.btn_clear.clicked.connect(self._on_clear)

        if self.cb_src:
            self.cb_src.currentIndexChanged.connect(self._disable_auto)
        if self.cb_level:
            self.cb_level.currentIndexChanged.connect(self._disable_auto)
        if self.edit_search:
            self.edit_search.textEdited.connect(self._disable_auto)

        # ===== Timer =====
        self.timer = QTimer(self)
        self.timer.setInterval(AUTO_REFRESH_MS)
        self.timer.timeout.connect(self._auto_refresh)
        self.timer.start()

        # 첫 로딩
        self._load_recent()

    # -------------------------
    # UI helpers
    # -------------------------
    def _setup_table(self):
        t = self.table
        assert t is not None

        t.setColumnCount(5)
        t.setHorizontalHeaderLabels(["TIME", "SRC", "LEVEL", "EVENT", "DETAIL"])

        t.verticalHeader().setVisible(False)
        t.setCornerButtonEnabled(False)

        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        h = t.horizontalHeader()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # TIME
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # SRC
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # LEVEL
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # EVENT
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)  # DETAIL

        t.setAlternatingRowColors(False)
        t.setSortingEnabled(False)

    def _apply_dark_lock_style(self):
        self.setStyleSheet(
            self.styleSheet()
            + """
        QTableWidget {
            selection-background-color: #1E1E1E;
            selection-color: #FFFFFF;
        }
        QTableWidget::item:selected {
            background-color: #1E1E1E;
            color: #FFFFFF;
        }
        QTextEdit#text_detail {
            background-color: #101010;
            color: #FFFFFF;
            border: 1px solid #2A2A2A;
            border-radius: 12px;
        }
        QComboBox#cb_source, QComboBox#cb_level, QLineEdit#edit_search {
            background-color: #101010;
            color: #FFFFFF;
        }
        QComboBox QAbstractItemView {
            background-color: #101010;
            color: #FFFFFF;
            selection-background-color: #222222;
            selection-color: #FFFFFF;
            outline: none;
        }
        """
        )

    # -------------------------
    # Filter / state
    # -------------------------
    def _disable_auto(self, *args):
        self.auto_mode = False

    def _current_filter(self) -> tuple[str, str, str]:
        src = self.cb_src.currentText().strip() if self.cb_src else "ALL"
        level = self.cb_level.currentText().strip() if self.cb_level else "ALL LEVEL"
        q = self.edit_search.text().strip() if self.edit_search else ""
        return (src, level, q)

    # -------------------------
    # Data load
    # -------------------------
    def _load_recent(self):
        data = _http_get_json(f"{API_RECENT}?limit={RECENT_LIMIT}")
        if data is None:
            return

        items = data.get("items", []) or []
        if not items:
            return

        try:
            top_id = max(int(it.get("id", 0)) for it in items)
        except Exception:
            top_id = 0

        if top_id != 0 and top_id == self._last_top_id:
            return

        self._last_top_id = top_id
        self._set_events(items)

    def _filter_items(self, items: list[dict], src: str, level: str, q: str) -> list[dict]:
        src_u = (src or "ALL").strip().upper()
        level_u = (level or "ALL LEVEL").strip().upper()
        q_u = (q or "").strip().upper()

        out: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue

            actor = str(it.get("actor") or it.get("src") or "").strip()
            lvl = str(it.get("level") or "").strip()
            evt = str(it.get("event_type") or it.get("event") or "").strip()
            msg = str(it.get("message") or it.get("detail") or "").strip()

            if src_u not in ("", "ALL") and actor.upper() != src_u:
                continue
            if level_u not in ("", "ALL", "ALL LEVEL") and lvl.upper() != level_u:
                continue

            if q_u:
                hay = f"{actor} {lvl} {evt} {msg}".upper()
                if q_u not in hay:
                    continue

            out.append(it)

        return out

    def _query(self, src: str, level: str, q: str):
        # 서버 query 엔드포인트 의존 없이 recent + 로컬 필터로 일관 처리
        data = _http_get_json(f"{API_RECENT}?limit={RECENT_LIMIT}")
        if data is None:
            return
        items = data.get("items", []) or []
        self._set_events(self._filter_items(items, src, level, q))

    def _map_item_to_event(self, it: dict) -> EventRow:
        """
        서버 응답 키가 두 가지 계열일 수 있어 둘 다 커버.
        1) 신형(events/recent): ts, actor, level, event_type, message
        2) 구형(DB 직접/alias): created_at, src, level, event, detail
        """
        _id = int(it.get("id", 0) or 0)

        created_at = str(it.get("ts") or it.get("created_at") or "")
        src = str(it.get("actor") or it.get("src") or "")
        level = str(it.get("level") or "")
        event = str(it.get("event_type") or it.get("event") or "")
        detail = str(it.get("message") or it.get("detail") or "")

        return EventRow(
            id=_id,
            created_at=created_at,
            src=src,
            level=level,
            event=event,
            detail=detail,
        )

    def _set_events(self, items):
        evs: list[EventRow] = []
        for it in items:
            if not isinstance(it, dict):
                continue

            try:
                ev = self._map_item_to_event(it)
            except Exception:
                continue

            # ✅ DEBUG/기타 레벨 제거
            if (ev.level or "").upper() not in ALLOW_LEVELS:
                continue

            ev = self._to_korean(ev)
            evs.append(ev)

        evs.sort(key=lambda x: x.id, reverse=True)
        evs = evs[:MAX_GUI_ROWS]

        sig = (
            evs[0].id if evs else 0,
            len(evs),
            evs[1].id if len(evs) > 1 else 0,
        )
        if sig == getattr(self, "_last_render_sig", None):
            return
        self._last_render_sig = sig

        self.events = evs
        self._render()

    # -------------------------
    # Render
    # -------------------------
    def _render(self):
        t = self.table
        assert t is not None

        t.setUpdatesEnabled(False)
        t.blockSignals(True)
        try:
            t.setRowCount(len(self.events))
            fm = QFontMetrics(t.font())
            max_px = max(240, t.columnWidth(4) - 24)

            for r, ev in enumerate(self.events):
                t.setItem(r, 0, QTableWidgetItem(ev.created_at))
                t.setItem(r, 1, QTableWidgetItem(ev.src))
                t.setItem(r, 2, QTableWidgetItem(ev.level))
                t.setItem(r, 3, QTableWidgetItem(ev.event))
                elided = fm.elidedText(
                    ev.detail or "", Qt.TextElideMode.ElideRight, max_px
                )
                t.setItem(r, 4, QTableWidgetItem(elided))

            t.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        finally:
            t.blockSignals(False)
            t.setUpdatesEnabled(True)

        if self.events:
            if t.currentRow() < 0:
                t.selectRow(0)
                self._show_detail(self.events[0])
        else:
            self._show_detail(EventRow(0, "--", "--", "--", "--", ""))

    def _show_detail(self, ev: EventRow):
        if self.lbl_time:
            self.lbl_time.setText(ev.created_at or "--")
        if self.lbl_src:
            self.lbl_src.setText(ev.src or "--")
        if self.lbl_level:
            self.lbl_level.setText(ev.level or "--")
        if self.lbl_event:
            self.lbl_event.setText(ev.event or "--")
        if self.text_detail:
            self.text_detail.setPlainText(ev.detail or "")

    # -------------------------
    # UI Events
    # -------------------------
    def _on_row_clicked(self, row: int, _col: int):
        if 0 <= row < len(self.events):
            self._show_detail(self.events[row])

    def _on_apply(self):
        self.auto_mode = False
        src, level, q = self._current_filter()
        self._query(src, level, q)

    def _on_clear(self):
        if self.cb_src:
            self.cb_src.setCurrentText("ALL")
        if self.cb_level:
            self.cb_level.setCurrentText("ALL LEVEL")
        if self.edit_search:
            self.edit_search.clear()

        self.auto_mode = True
        self._load_recent()

    def _auto_refresh(self):
        if self.auto_mode and self._current_filter() == self._baseline:
            self._load_recent()

    def _to_korean(self, ev: EventRow) -> EventRow:
        e = (ev.event or "").upper()

        event_ko = {
            # Pinky
            "PINKY_CMD_ENQ": "핑키 명령 등록",
            "PINKY_CMD_ENQ_SUPPRESS": "핑키 명령 중복 억제",
            "PINKY_CMD_CLAIMED": "핑키 명령 수신",
            "PINKY_CMD_ACK": "핑키 명령 완료",
            # Arm
            "ARM_CMD_ENQ": "로봇팔 명령 등록",
            "ARM_CMD_ENQ_SUPPRESS": "로봇팔 명령 중복 억제",
            "ARM_CMD_QUEUED": "로봇팔 명령 큐 등록",
            "ARM_CMD_CLAIMED": "로봇팔 명령 수신",
            "ARM_CMD_ACK": "로봇팔 명령 완료",
            "ARM_DETECTED": "로봇팔 감지",
            "ARM_WARN_SET": "로봇팔 경고 발생",
            "ARM_WARN_CLEARED": "로봇팔 경고 해제",
            "ARM_CMD_DROPPED": "로봇팔 명령 무시",
            # Autos sequence
            "AUTOSEQ_AFTER_CHARGE_SKIPPED": "후속 자동명령 생략",
        }

        if e in event_ko:
            ev.event = event_ko[e]

        if e in ("ESTOP", "E_STOP", "EMERGENCY_STOP"):
            ev.level = "ERROR"
            ev.detail = "비상정지(E-STOP) 발생"
            return ev

        if e in ("PAYMENT_PAID", "PAID"):
            ev.detail = "결제 확정: PAID"
            return ev

        return ev
