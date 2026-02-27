# admin_app/pages/status_page.py
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from PyQt6 import uic
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QProgressBar,
    QFrame,
    QVBoxLayout,
    QTextEdit,
)

from ..utils.paths import ui_path


# FastAPI 서버 주소 (GUI는 "클라이언트"임)
FASTAPI_BASE = "http://192.168.1.8:8000"

# ✅ FIX: 서버에서 실제로 받는 파라미터는 robot_id 입니다.
ARM_STATE_URL = f"{FASTAPI_BASE}/api/arm/state?robot_id=jetcobot1"

# (요청하신대로) 다른 건 건드리지 않습니다.
PINKY_STATE_URL = f"{FASTAPI_BASE}/api/pinky/state"
EVENTS_RECENT_URL = f"{FASTAPI_BASE}/events/recent"

ONLINE_THRESHOLD_SEC = 8.0  # updated_at 기준 8초 이내면 ONLINE


def _http_get_json(url: str, timeout_s: float = 0.8) -> dict | None:
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def _http_post_json(url: str, payload: dict, timeout_s: float = 0.8) -> dict | None:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def _fetch_recent_event_items(limit: int = 240) -> list[dict]:
    data = _http_get_json(
        f"{EVENTS_RECENT_URL}?limit={max(1, min(int(limit), 500))}",
        timeout_s=1.0,
    )
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


def _parse_iso_dt(s: str) -> float | None:
    if not s:
        return None
    try:
        ss = str(s).strip()
        if ss.endswith("Z"):
            ss = ss[:-1] + "+00:00"

        dt = datetime.fromisoformat(ss)

        if dt.tzinfo is None:
            local_tz = datetime.now().astimezone().tzinfo
            dt = dt.replace(tzinfo=local_tz)

        return dt.timestamp()
    except Exception:
        return None


def _online_from_updated_at(updated_at_iso: str | None) -> bool:
    ts = _parse_iso_dt(updated_at_iso or "")
    if ts is None:
        return False
    diff = time.time() - ts
    return diff <= ONLINE_THRESHOLD_SEC


def _fmt_hhmm_from_iso(s: str | None) -> str:
    if not s:
        return ""
    try:
        ss = str(s).strip()
        if ss.endswith("Z"):
            ss = ss[:-1] + "+00:00"
        dt = datetime.fromisoformat(ss)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


# =========================
# ✅ ARM: 로봇팔 전용 파서(최소/확실)
# =========================
def _arm_pick_mode(st: dict) -> str:
    # arm 쪽에서 흔히 나오는 키들을 우선순위로
    for k in ("state", "fsm_state", "mode", "status", "phase"):
        v = st.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return "--"


def _arm_pick_now(st: dict) -> str:
    cc = st.get("current_command")
    if isinstance(cc, dict):
        cmd = str(cc.get("command") or "").strip()
        status = str(cc.get("status") or "").strip().upper()
        if cmd:
            return f"{status}: {cmd}" if status else cmd

    # arm은 job/warn이 중요하니 여기 우선
    for k in ("current_event", "job", "warn", "now_event", "event", "message", "detail", "reason"):
        v = st.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return "--"


def _arm_recent3(st: dict) -> list[dict]:
    """
    ✅ ARM은 server state 구조가 들쭉날쭉할 수 있으니,
    - state.recent_events / state.events / recent_events / events가 있으면 3개 뽑고
    - 없으면 'job/warn' 기반으로 1줄이라도 만든다
    반환: [{line:str, raw:any}, ...] 길이 3 보장
    """
    candidates = [
        st.get("recent_events"),
        st.get("recent"),
        st.get("events"),
    ]

    events = None
    for c in candidates:
        if isinstance(c, list) and c:
            events = c
            break

    out: list[dict] = []
    if isinstance(events, list):
        for e in events[:3]:
            if isinstance(e, str):
                s = e.strip()
                if s:
                    out.append({"line": s[:60], "raw": e})
                continue
            if isinstance(e, dict):
                t = (
                    _fmt_hhmm_from_iso(e.get("time"))
                    or _fmt_hhmm_from_iso(e.get("ts"))
                    or _fmt_hhmm_from_iso(e.get("created_at"))
                    or _fmt_hhmm_from_iso(e.get("updated_at"))
                    or ""
                )
                msg = ""
                for k in ("event", "msg", "message", "name", "type", "title", "action", "job", "warn"):
                    v = e.get(k)
                    if v is not None and str(v).strip():
                        msg = str(v).strip()
                        break
                msg = (msg or "--")[:54]
                line = f"{t}  {msg}" if t else msg
                out.append({"line": line, "raw": e})

    # fallback: job/warn라도 넣기
    if not out:
        j = st.get("job")
        w = st.get("warn")
        base = ""
        if j and str(j).strip():
            base = str(j).strip()
        if w and str(w).strip():
            base = f"{base} · {str(w).strip()}" if base else str(w).strip()
        if base:
            out.append({"line": f"--  {base[:54]}", "raw": {"job": j, "warn": w}})

    while len(out) < 3:
        out.append({"line": "--", "raw": None})
    return out[:3]


# =========================
# (이 아래부터는 기존 구조 유지)
# =========================
def _fetch_pinky_state(robot_id: str) -> dict | None:
    d = _http_get_json(f"{PINKY_STATE_URL}?robot_id={robot_id}")
    if d:
        return d
    d = _http_post_json(PINKY_STATE_URL, {"robot_id": robot_id})
    if d:
        return d
    d = _http_post_json(PINKY_STATE_URL, {"id": robot_id})
    if d:
        return d
    return None


def _to_f(v):
    try:
        return float(v)
    except Exception:
        return None


def _pick_mode(state: dict) -> str:
    for k in ("mode", "fsm_state", "state", "status", "phase"):
        v = state.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return "--"


def _pick_now_event(state: dict) -> str:
    for k in ("now_event", "current_event", "event", "last_event", "last_transition", "reason", "job"):
        v = state.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return "--"


def _extract_recent3_items(data: dict) -> list[dict]:
    st = (data.get("state") or {}) if isinstance(data, dict) else {}
    candidates = [
        st.get("recent_events"),
        st.get("recent"),
        st.get("events"),
        data.get("recent_events") if isinstance(data, dict) else None,
        data.get("events") if isinstance(data, dict) else None,
    ]

    events = None
    for c in candidates:
        if isinstance(c, list) and c:
            events = c
            break

    out: list[dict] = []

    if isinstance(events, list):
        for e in events[:3]:
            if isinstance(e, str):
                s = e.strip()
                if s:
                    out.append({"line": s, "raw": e})
                continue

            if isinstance(e, dict):
                t = (
                    _fmt_hhmm_from_iso(e.get("time"))
                    or _fmt_hhmm_from_iso(e.get("ts"))
                    or _fmt_hhmm_from_iso(e.get("created_at"))
                    or _fmt_hhmm_from_iso(e.get("updated_at"))
                    or ""
                )

                msg = ""
                for k in ("event", "msg", "message", "name", "type", "title", "action"):
                    v = e.get(k)
                    if v is not None and str(v).strip():
                        msg = str(v).strip()
                        break

                detail = ""
                for k in ("detail", "info", "desc", "note", "reason"):
                    v = e.get(k)
                    if v is not None and str(v).strip():
                        detail = str(v).strip()
                        break

                text = msg if msg else "--"
                if detail and detail != msg:
                    if len(detail) <= 18 and len(text) <= 18:
                        text = f"{text} · {detail}"

                text = text[:48]
                line = f"{t}  {text}" if t else text
                out.append({"line": line, "raw": e})

    if not out:
        now = _pick_now_event(st)
        if now and now != "--":
            out = [{"line": f"--  {now}", "raw": {"event": now}}]

    while len(out) < 3:
        out.append({"line": "--", "raw": None})
    return out[:3]


def _match_event_source(it: dict, source: str) -> bool:
    rid = str(it.get("robot_id") or "").strip().lower()
    actor = str(it.get("actor") or it.get("src") or "").strip().lower()
    msg = str(it.get("message") or it.get("detail_raw") or it.get("detail") or "").strip().lower()

    if source == "TASHOBOT-1":
        return rid == "pinky1" or actor == "pinky1" or "robot_id=pinky1" in msg

    if source == "TASHOBOT-2":
        return rid == "pinky2" or actor == "pinky2" or "robot_id=pinky2" in msg

    if source == "CHARM":
        return (
            rid == "jetcobot1"
            or actor in ("arm", "jetcobot1")
            or "client_id=jetcobot1" in msg
            or "robot_id=jetcobot1" in msg
        )

    return False


def _recent3_from_event_log(items: list[dict], source: str) -> list[dict]:
    out: list[dict] = []

    for it in items:
        if not _match_event_source(it, source):
            continue

        t = _fmt_hhmm_from_iso(it.get("ts")) or _fmt_hhmm_from_iso(it.get("created_at")) or ""
        event_name = str(it.get("event_type") or it.get("event") or "--").strip() or "--"
        detail = str(it.get("message") or it.get("detail_raw") or it.get("detail") or "").strip()

        text = event_name
        if detail and detail != event_name and len(detail) <= 22 and len(text) <= 22:
            text = f"{text} · {detail}"

        out.append({"line": f"{t}  {text[:54]}" if t else text[:54], "raw": it})
        if len(out) >= 3:
            break

    while len(out) < 3:
        out.append({"line": "--", "raw": None})
    return out[:3]


class StatusPage(QWidget):
    def __init__(self):
        super().__init__()
        uic.loadUi(ui_path("Admin_STATUS.ui"), self)

        # ===== header =====
        self._lbl_clock: QLabel | None = self.findChild(QLabel, "lbl_clock")
        self._lbl_system_health: QLabel | None = self.findChild(QLabel, "lbl_system_health")

        # ===== rename titles (top cards) =====
        self._lbl_car1_title: QLabel | None = self.findChild(QLabel, "lbl_car1_title")
        self._lbl_car2_title: QLabel | None = self.findChild(QLabel, "lbl_car2_title")
        self._lbl_arm_title: QLabel | None = self.findChild(QLabel, "lbl_arm_title")

        # ===== rename titles (bottom logs) =====
        self._lbl_car1_log_title: QLabel | None = self.findChild(QLabel, "lbl_car1_log_title")
        self._lbl_car2_log_title: QLabel | None = self.findChild(QLabel, "lbl_car2_log_title")
        self._lbl_arm_log_title: QLabel | None = self.findChild(QLabel, "lbl_arm_log_title")

        # ===== Detail Panel (dynamic) =====
        self._detail_frame: QFrame | None = None
        self._detail_text: QTextEdit | None = None
        self._install_detail_panel()

        # ===== ARM =====
        self._lbl_detect: QLabel | None = self.findChild(QLabel, "lbl_arm_detect_val")
        if self._lbl_detect is None:
            raise RuntimeError("[STATUS] lbl_arm_detect_val 를 찾지 못했습니다.")

        self._arm = {
            "conn": self.findChild(QLabel, "lbl_arm_conn"),
            "mode": self.findChild(QLabel, "lbl_arm_mode_val"),
            "now": self.findChild(QLabel, "lbl_arm_now_evt"),
            "log1": self.findChild(QLabel, "lbl_arm_log1"),
            "log2": self.findChild(QLabel, "lbl_arm_log2"),
            "log3": self.findChild(QLabel, "lbl_arm_log3"),
        }

        # ===== CARS ===== (요청대로 수정 안 함)
        self._cars = {
            1: {
                "conn": self.findChild(QLabel, "lbl_car1_conn"),
                "batt": self.findChild(QProgressBar, "bar_car1_batt"),
                "fsm": self.findChild(QLabel, "lbl_car1_fsm_val"),
                "now": self.findChild(QLabel, "lbl_car1_now_evt"),
                "log1": self.findChild(QLabel, "lbl_car1_log1"),
                "log2": self.findChild(QLabel, "lbl_car1_log2"),
                "log3": self.findChild(QLabel, "lbl_car1_log3"),
            },
            2: {
                "conn": self.findChild(QLabel, "lbl_car2_conn"),
                "batt": self.findChild(QProgressBar, "bar_car2_batt"),
                "fsm": self.findChild(QLabel, "lbl_car2_fsm_val"),
                "now": self.findChild(QLabel, "lbl_car2_now_evt"),
                "log1": self.findChild(QLabel, "lbl_car2_log1"),
                "log2": self.findChild(QLabel, "lbl_car2_log2"),
                "log3": self.findChild(QLabel, "lbl_car2_log3"),
            },
        }

        self._validate_widgets()

        self._last_external_update_ts = 0.0
        self._recent_raw: dict[tuple[str, int], object | None] = {}

        self._apply_branding_titles()
        self._install_clickable_logs()

        # ---- initial ----
        self.set_arm_detection(False, from_external=False)
        self._apply_pinky_state(None, which=1)
        self._apply_pinky_state(None, which=2)
        self._apply_arm_state(None)
        self._set_detail_default()

        # ---- timers ----
        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._poll_all_states)
        self._timer.start()

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(500)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()

    def _validate_widgets(self):
        missing = []
        if self._lbl_car1_title is None:
            missing.append("lbl_car1_title")
        if self._lbl_car2_title is None:
            missing.append("lbl_car2_title")
        if self._lbl_arm_title is None:
            missing.append("lbl_arm_title")
        if self._lbl_car1_log_title is None:
            missing.append("lbl_car1_log_title")
        if self._lbl_car2_log_title is None:
            missing.append("lbl_car2_log_title")
        if self._lbl_arm_log_title is None:
            missing.append("lbl_arm_log_title")

        for k, w in self._arm.items():
            if w is None:
                missing.append(f"arm.{k}")

        for which, car in self._cars.items():
            for k, w in car.items():
                if w is None:
                    missing.append(f"car{which}.{k}")

        if missing:
            raise RuntimeError(f"[STATUS] UI 위젯 objectName 누락/오타: {', '.join(missing)}")

    def _tick_clock(self):
        if self._lbl_clock is None:
            return
        now = datetime.now().strftime("%H:%M:%S")
        self._lbl_clock.setText(f"TIME: {now}")

    def _apply_branding_titles(self):
        if self._lbl_car1_title:
            self._lbl_car1_title.setText("TASHOBOT-1")
        if self._lbl_car2_title:
            self._lbl_car2_title.setText("TASHOBOT-2")
        if self._lbl_arm_title:
            self._lbl_arm_title.setText("CHARM")

        if self._lbl_car1_log_title:
            self._lbl_car1_log_title.setText("Activity Feed · TASHOBOT-1")
        if self._lbl_car2_log_title:
            self._lbl_car2_log_title.setText("Activity Feed · TASHOBOT-2")
        if self._lbl_arm_log_title:
            self._lbl_arm_log_title.setText("Activity Feed · CHARM")

    # -------------------------
    # Detail panel / clickable logs (기존 그대로)
    # -------------------------
    def _install_detail_panel(self):
        root_layout: QVBoxLayout | None = self.findChild(QVBoxLayout, "layout_root")
        if root_layout is None:
            raise RuntimeError("[STATUS] layout_root 를 찾지 못했습니다. (Admin_STATUS.ui 확인 필요)")

        frame = QFrame()
        frame.setObjectName("card_detail")
        frame.setStyleSheet(
            """
            QFrame#card_detail {
              background-color: #121212;
              border: 1px solid #222222;
              border-radius: 16px;
            }
            QLabel#lbl_detail_title {
              font-size: 12pt;
              font-weight: bold;
              color: #DDDDDD;
            }
            QTextEdit#txt_detail {
              background-color: #0F0F0F;
              border: 1px solid #2A2A2A;
              border-radius: 12px;
              color: #DDDDDD;
              font-size: 10pt;
              padding: 8px;
            }
            """
        )

        v = QVBoxLayout(frame)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        title = QLabel("Event Detail")
        title.setObjectName("lbl_detail_title")

        txt = QTextEdit()
        txt.setObjectName("txt_detail")
        txt.setReadOnly(True)
        txt.setMinimumHeight(150)

        v.addWidget(title)
        v.addWidget(txt)

        insert_at = max(0, root_layout.count() - 1)
        root_layout.insertWidget(insert_at, frame)

        self._detail_frame = frame
        self._detail_text = txt

    def _set_detail_default(self):
        if self._detail_text is None:
            return
        self._detail_text.setPlainText("Activity Feed 항목을 클릭하면 여기에 상세가 표시됩니다.")

    def _show_detail(self, source: str, idx: int):
        if self._detail_text is None:
            return

        raw = self._recent_raw.get((source, idx))
        header = f"[{source}]  item {idx}\n"
        sep = "-" * 44 + "\n"

        if raw is None:
            self._detail_text.setPlainText(
                header + sep + "상세 정보가 없습니다. (서버가 raw event를 제공하지 않았거나 '--' 항목입니다.)"
            )
            return

        if isinstance(raw, dict):
            try:
                body = json.dumps(raw, ensure_ascii=False, indent=2)
            except Exception:
                body = str(raw)
        else:
            body = str(raw)

        self._detail_text.setPlainText(header + sep + body)

    def _install_clickable_logs(self):
        self._make_clickable_label(self._cars[1]["log1"], "TASHOBOT-1", 1)
        self._make_clickable_label(self._cars[1]["log2"], "TASHOBOT-1", 2)
        self._make_clickable_label(self._cars[1]["log3"], "TASHOBOT-1", 3)

        self._make_clickable_label(self._cars[2]["log1"], "TASHOBOT-2", 1)
        self._make_clickable_label(self._cars[2]["log2"], "TASHOBOT-2", 2)
        self._make_clickable_label(self._cars[2]["log3"], "TASHOBOT-2", 3)

        self._make_clickable_label(self._arm["log1"], "CHARM", 1)
        self._make_clickable_label(self._arm["log2"], "CHARM", 2)
        self._make_clickable_label(self._arm["log3"], "CHARM", 3)

    def _make_clickable_label(self, lbl: QLabel, source: str, idx: int):
        if lbl is None:
            return

        lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        def _on_click(_ev):
            self._show_detail(source, idx)

        lbl.mousePressEvent = _on_click  # type: ignore[attr-defined]

    # -------------------------
    # ✅ Polling: ARM은 URL만 고치면 바로 붙음
    # -------------------------
    def _poll_all_states(self):
        event_items = _fetch_recent_event_items(limit=240)

        # 1) ARM
        data_arm = _http_get_json(ARM_STATE_URL)
        arm_log_items = _recent3_from_event_log(event_items, "CHARM")
        if data_arm and data_arm.get("ok"):
            st = data_arm.get("state") if isinstance(data_arm.get("state"), dict) else {}
            detected = bool(st.get("detected", data_arm.get("detected", False)))
            self.set_arm_detection(detected, from_external=False)
            self._apply_arm_state(data_arm, log_items=arm_log_items)
        else:
            self._apply_arm_state(None, log_items=arm_log_items)

        # 2) PINKY는 건드리지 않음 (요청)
        data_p1 = _fetch_pinky_state("pinky1")
        self._apply_pinky_state(
            data_p1,
            which=1,
            log_items=_recent3_from_event_log(event_items, "TASHOBOT-1"),
        )

        data_p2 = _fetch_pinky_state("pinky2")
        self._apply_pinky_state(
            data_p2,
            which=2,
            log_items=_recent3_from_event_log(event_items, "TASHOBOT-2"),
        )

        # 시스템 건강 라벨도 기존 유지
        if self._lbl_system_health:
            c1 = (self._cars[1]["conn"].text() if self._cars[1]["conn"] else "")
            c2 = (self._cars[2]["conn"].text() if self._cars[2]["conn"] else "")
            ca = (self._arm["conn"].text() if self._arm["conn"] else "")

            all_ok = (c1 == "OK") and (c2 == "OK") and (ca == "OK")
            any_off = ("OFFLINE" in (c1, c2, ca))

            if all_ok:
                self._lbl_system_health.setText("SYSTEM: OK")
                self._lbl_system_health.setStyleSheet("color:#6EEB83; font-size: 11pt; font-weight: bold;")
            elif any_off:
                self._lbl_system_health.setText("SYSTEM: OFFLINE")
                self._lbl_system_health.setStyleSheet("color:#FF4D4D; font-size: 11pt; font-weight: bold;")
            else:
                self._lbl_system_health.setText("SYSTEM: STALE")
                self._lbl_system_health.setStyleSheet("color:#FFD166; font-size: 11pt; font-weight: bold;")

    # =========================
    # ✅ Apply: ARM (여기만 확실히)
    # =========================
    def _apply_arm_state(self, data: dict | None, log_items: list[dict] | None = None):
        conn: QLabel = self._arm["conn"]
        mode_lbl: QLabel = self._arm["mode"]
        now_lbl: QLabel = self._arm["now"]
        log1: QLabel = self._arm["log1"]
        log2: QLabel = self._arm["log2"]
        log3: QLabel = self._arm["log3"]

        if not data or not data.get("ok"):
            conn.setText("OFFLINE")
            conn.setStyleSheet("color:#FF4D4D; font-weight:bold;")
            mode_lbl.setText("--")
            now_lbl.setText("--")

            items = log_items if isinstance(log_items, list) else []
            if not items:
                items = [{"line": "--", "raw": None}, {"line": "--", "raw": None}, {"line": "--", "raw": None}]

            log1.setText(items[0]["line"])
            log2.setText(items[1]["line"])
            log3.setText(items[2]["line"])

            self._recent_raw[("CHARM", 1)] = items[0]["raw"]
            self._recent_raw[("CHARM", 2)] = items[1]["raw"]
            self._recent_raw[("CHARM", 3)] = items[2]["raw"]
            return

        st = data.get("state") if isinstance(data, dict) else None
        if not isinstance(st, dict):
            st = {}

        updated_at = st.get("updated_at") or data.get("updated_at")

        if updated_at:
            if _online_from_updated_at(updated_at):
                conn.setText("OK")
                conn.setStyleSheet("color:#6EEB83; font-weight:bold;")
            else:
                conn.setText("STALE")
                conn.setStyleSheet("color:#FFD166; font-weight:bold;")
        else:
            # updated_at 없으면 "응답은 왔다" = STALE로 처리(과장하지 않음)
            conn.setText("STALE")
            conn.setStyleSheet("color:#FFD166; font-weight:bold;")

        mode_lbl.setText(_arm_pick_mode(st))
        now_lbl.setText(_arm_pick_now(st))

        items = log_items if isinstance(log_items, list) else None
        if not items or all(it.get("raw") is None for it in items if isinstance(it, dict)):
            items = _arm_recent3(st)
        log1.setText(items[0]["line"])
        log2.setText(items[1]["line"])
        log3.setText(items[2]["line"])

        self._recent_raw[("CHARM", 1)] = items[0]["raw"]
        self._recent_raw[("CHARM", 2)] = items[1]["raw"]
        self._recent_raw[("CHARM", 3)] = items[2]["raw"]

    # =========================
    # Apply: PINKY (원본 유지)
    # =========================
    def _apply_pinky_state(self, data: dict | None, which: int, log_items: list[dict] | None = None):
        car = self._cars.get(which)
        if not car:
            return

        conn: QLabel = car["conn"]
        batt: QProgressBar = car["batt"]
        fsm_lbl: QLabel = car["fsm"]
        now_lbl: QLabel = car["now"]
        log1: QLabel = car["log1"]
        log2: QLabel = car["log2"]
        log3: QLabel = car["log3"]

        source = "TASHOBOT-1" if which == 1 else "TASHOBOT-2"

        if not data or not data.get("ok"):
            conn.setText("OFFLINE")
            conn.setStyleSheet("color:#FF4D4D; font-weight:bold;")

            fsm_lbl.setText("--")
            now_lbl.setText("--")
            batt.setValue(0)
            batt.setFormat("BATTERY --%")

            items = log_items if isinstance(log_items, list) else []
            if not items:
                items = [{"line": "--", "raw": None}, {"line": "--", "raw": None}, {"line": "--", "raw": None}]

            log1.setText(items[0]["line"])
            log2.setText(items[1]["line"])
            log3.setText(items[2]["line"])

            self._recent_raw[(source, 1)] = items[0]["raw"]
            self._recent_raw[(source, 2)] = items[1]["raw"]
            self._recent_raw[(source, 3)] = items[2]["raw"]
            return

        st = data.get("state") or {}

        updated_at = st.get("updated_at") or data.get("updated_at")
        if not updated_at:
            conn.setText("OK")
            conn.setStyleSheet("color:#6EEB83; font-weight:bold;")
        else:
            online = _online_from_updated_at(updated_at)
            if online:
                conn.setText("OK")
                conn.setStyleSheet("color:#6EEB83; font-weight:bold;")
            else:
                conn.setText("STALE")
                conn.setStyleSheet("color:#FFD166; font-weight:bold;")

        fsm_lbl.setText(_pick_mode(st))
        now_lbl.setText(_pick_now_event(st))

        b = _to_f(st.get("battery_pct"))
        if b is None:
            batt.setValue(0)
            batt.setFormat("BATTERY --%")
        else:
            b_i = max(0, min(100, int(round(b))))
            batt.setValue(b_i)
            batt.setFormat(f"BATTERY {b_i}%")

        items = log_items if isinstance(log_items, list) else None
        if not items or all(it.get("raw") is None for it in items if isinstance(it, dict)):
            items = _extract_recent3_items(data)
        log1.setText(items[0]["line"])
        log2.setText(items[1]["line"])
        log3.setText(items[2]["line"])

        self._recent_raw[(source, 1)] = items[0]["raw"]
        self._recent_raw[(source, 2)] = items[1]["raw"]
        self._recent_raw[(source, 3)] = items[2]["raw"]

    # =========================
    # Public API
    # =========================
    def set_arm_detection(self, detected: bool, from_external: bool = True):
        detected = bool(detected)
        if from_external:
            self._last_external_update_ts = time.time()

        if detected:
            self._lbl_detect.setText("DETECTED")
            self._lbl_detect.setStyleSheet("color:#6EEB83; font-weight:bold; font-size:10pt;")
        else:
            self._lbl_detect.setText("UNDETECTED")
            self._lbl_detect.setStyleSheet("color:#FF4D4D; font-weight:bold; font-size:10pt;")
