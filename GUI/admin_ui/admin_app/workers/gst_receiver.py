# admin_app/workers/gst_receiver.py
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst


class GstReceiver(QThread):
    frame = pyqtSignal(np.ndarray)

    def __init__(
        self,
        port: int,
        jitter_latency_ms: int = 10,
        multicast_group: str | None = None,
        multicast_iface: str | None = None,
    ):
        super().__init__()
        self.port = int(port)
        self.jitter_latency_ms = int(jitter_latency_ms)
        self.multicast_group = multicast_group
        self.multicast_iface = multicast_iface

        self._running = True
        self.pipeline = None
        self.appsink = None
        self._printed_first_frame = False

    def _build_udpsrc(self) -> str:
        if not self.multicast_group:
            return f"udpsrc port={self.port}"

        src = f"udpsrc multicast-group={self.multicast_group} auto-multicast=true port={self.port}"
        if self.multicast_iface:
            src += f" multicast-iface={self.multicast_iface}"
        return src

    def run(self):
        Gst.init(None)

        udpsrc_str = self._build_udpsrc()

        # ✅ 초저지연 파이프라인 핵심:
        # - rtpjitterbuffer latency=0 (또는 5~20으로만 최소 튜닝)
        # - drop-on-latency=true : 지연이 쌓이면 프레임 버림
        # - do-lost=true : 유실 처리
        # - queue leaky=downstream + max-size-buffers=1 : 항상 최신 프레임만
        # - appsink sync=false max-buffers=1 drop=true : UI는 최신만 표시
        pipeline_str = (
            f'{udpsrc_str} caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! '
            f'rtpjitterbuffer latency={self.jitter_latency_ms} drop-on-latency=true do-lost=true ! '
            f'rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! '
            f'queue leaky=downstream max-size-buffers=1 max-size-time=0 max-size-bytes=0 ! '
            f'video/x-raw,format=BGR ! '
            f'appsink name=appsink emit-signals=true sync=false max-buffers=1 drop=true'
        )

        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
        except Exception as e:
            print(f"[GstReceiver:{self.port}] parse_launch failed: {e}")
            print(f"[GstReceiver:{self.port}] pipeline_str={pipeline_str}")
            return

        self.appsink = self.pipeline.get_by_name("appsink")
        if self.appsink is None:
            print(f"[GstReceiver:{self.port}] appsink not found")
            return

        self.appsink.connect("new-sample", self.on_new_sample)

        bus = self.pipeline.get_bus()

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        mode = "MCAST" if self.multicast_group else "UCAST"
        print(f"[GstReceiver:{self.port}] mode={mode} set_state PLAYING => {ret.value_nick}")

        while self._running:
            msg = bus.timed_pop_filtered(
                10 * Gst.MSECOND,
                Gst.MessageType.ERROR | Gst.MessageType.WARNING | Gst.MessageType.EOS | Gst.MessageType.STATE_CHANGED,
            )
            if msg is not None:
                t = msg.type
                if t == Gst.MessageType.ERROR:
                    err, dbg = msg.parse_error()
                    print(f"[GstReceiver:{self.port}] GST ERROR: {err} / {dbg}")
                elif t == Gst.MessageType.WARNING:
                    err, dbg = msg.parse_warning()
                    # 너무 시끄러우면 WARNING 출력은 꺼도 됩니다.
                    print(f"[GstReceiver:{self.port}] GST WARNING: {err} / {dbg}")
                elif t == Gst.MessageType.EOS:
                    print(f"[GstReceiver:{self.port}] EOS")
                elif t == Gst.MessageType.STATE_CHANGED:
                    if msg.src == self.pipeline:
                        old, new, pending = msg.parse_state_changed()
                        # 디버그 과다하면 주석처리 가능
                        # print(f"[GstReceiver:{self.port}] STATE: {old.value_nick} -> {new.value_nick}")

            self.msleep(5)

        try:
            if self.pipeline is not None:
                self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
        print(f"[GstReceiver:{self.port}] stopped")

    def on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        s = caps.get_structure(0)
        w = s.get_value("width")
        h = s.get_value("height")

        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK

        try:
            frame = np.frombuffer(mapinfo.data, dtype=np.uint8).reshape((h, w, 3))
            if not self._printed_first_frame:
                self._printed_first_frame = True
                print(f"[GstReceiver:{self.port}] first frame received: {w}x{h}")

            # ✅ 안전하게 copy (UI 스레드로 넘어갈 때 버퍼 lifetime 이슈 방지)
            self.frame.emit(frame.copy())
        finally:
            buf.unmap(mapinfo)

        return Gst.FlowReturn.OK

    def stop(self):
        self._running = False
