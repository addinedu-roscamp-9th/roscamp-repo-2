#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import time

import cv2
import gi
import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String
from ultralytics import YOLO

gi.require_version("Gst", "1.0")
from gi.repository import Gst

# ==============================
# 🔴 모델 경로 고정
# ==============================
MODEL_PATH = "/home/cwj/roscamp-repo-2/git/server/ai_server/models/goat.pt"

LABEL_CANONICAL_MAP = {
    "pinky_63fb": "pinky_63",
    "pinky_15e2": "pinky_15",
}

DEFAULT_IN_PORT = 5000
DEFAULT_PAYLOAD = 96


def build_in_pipeline(port: int, payload: int = 96) -> str:
    return (
        f"udpsrc port={port} "
        f"caps=application/x-rtp,media=video,encoding-name=H264,payload={payload},clock-rate=90000 ! "
        "rtpjitterbuffer latency=30 drop-on-latency=true do-lost=true ! "
        "rtph264depay ! h264parse ! avdec_h264 ! "
        "videoconvert ! video/x-raw,format=BGR ! "
        "appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true"
    )


def build_out_pipeline(
    host: str,
    port: int,
    width: int,
    height: int,
    fps: int,
    payload: int = 96,
    bitrate_kbps: int = 1200,
) -> str:
    # appsrc -> x264enc -> RTP/H264 -> udpsink
    return (
        "appsrc name=src is-live=true block=true format=time do-timestamp=true ! "
        f"video/x-raw,format=BGR,width={width},height={height},framerate={fps}/1 ! "
        "videoconvert ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate_kbps} key-int-max=10 bframes=0 ! "
        "h264parse ! "
        f"rtph264pay config-interval=1 pt={payload} mtu=1200 ! "
        f"udpsink host={host} port={port} sync=false async=false"
    )


class UnifiedYoloRedStop(Node):
    def __init__(
        self,
        in_port: int,
        out_host: str,
        out_port: int,
        fps: int,
        bitrate: int,
        payload: int,
        no_preview: bool,
        labels_topic: str,
        stop_topic: str,
        node_name: str,
    ):
        super().__init__(node_name)

        self.no_preview = no_preview
        self.fps = fps
        self.payload = payload
        self.out_host = out_host
        self.out_port = out_port
        self.bitrate = bitrate

        # ==============================
        # ROS Publishers
        # ==============================
        self.pub_labels = self.create_publisher(String, labels_topic, 10)
        self.pub_stop = self.create_publisher(Bool, stop_topic, 10)

        # ==============================
        # YOLO
        # ==============================
        self.get_logger().info(f"Loading YOLO model: {MODEL_PATH}")
        self.model = YOLO(MODEL_PATH)
        self.conf = 0.5

        # ==============================
        # GStreamer init
        # ==============================
        Gst.init(None)

        # ==============================
        # INPUT pipeline (appsink)
        # ==============================
        in_pipeline = build_in_pipeline(in_port, payload=self.payload)
        self.pipeline_in = Gst.parse_launch(in_pipeline)
        self.appsink = self.pipeline_in.get_by_name("sink")
        self.pipeline_in.set_state(Gst.State.PLAYING)

        # frame size will be known after first sample
        self.out_ready = False
        self.pipeline_out = None
        self.appsrc = None

        # ==============================
        # RED detection config
        # ==============================
        self.roi_ratio_w = 0.35
        self.roi_ratio_h = 0.35
        self.min_red_area = 2500
        self.red_needed_frames = 4

        self.lower_red1 = np.array([0, 120, 70])
        self.upper_red1 = np.array([10, 255, 255])
        self.lower_red2 = np.array([170, 120, 70])
        self.upper_red2 = np.array([180, 255, 255])

        self.kernel = np.ones((5, 5), np.uint8)

        self.red_count = 0
        self.stop_active = False

        self.prev_time = time.time()

        # 30Hz 루프
        self.create_timer(1.0 / 30.0, self.tick)

        self.get_logger().info("Unified YOLO + RED STOP node started.")
        if not self.no_preview:
            self.get_logger().info("Press 'q' in preview window to exit.")

    def remap_label(self, raw: str) -> str:
        return LABEL_CANONICAL_MAP.get(raw, raw)

    # ==================================
    # Frame pull
    # ==================================
    def get_frame(self):
        sample = self.appsink.emit("try-pull-sample", 0)
        if sample is None:
            return None

        buf = sample.get_buffer()
        caps = sample.get_caps()
        s = caps.get_structure(0)
        w = s.get_value("width")
        h = s.get_value("height")

        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return None

        frame = np.frombuffer(mapinfo.data, dtype=np.uint8).reshape((h, w, 3))
        buf.unmap(mapinfo)
        return frame.copy()

    # ==================================
    # Output pipeline init after we know frame size
    # ==================================
    def ensure_out_pipeline(self, width: int, height: int):
        if self.out_ready:
            return

        out_pipeline = build_out_pipeline(
            host=self.out_host,
            port=self.out_port,
            width=width,
            height=height,
            fps=self.fps,
            payload=self.payload,
            bitrate_kbps=self.bitrate,
        )
        self.pipeline_out = Gst.parse_launch(out_pipeline)
        self.appsrc = self.pipeline_out.get_by_name("src")
        self.pipeline_out.set_state(Gst.State.PLAYING)

        self.out_ready = True
        self.get_logger().info(
            f"Overlay stream ON -> {self.out_host}:{self.out_port} (RTP/H264 pt={self.payload})"
        )

    # ==================================
    # Push frame to appsrc
    # ==================================
    def push_frame(self, bgr: np.ndarray):
        if not self.out_ready or self.appsrc is None:
            return

        h, w = bgr.shape[:2]
        data = bgr.tobytes()

        buf = Gst.Buffer.new_allocate(None, len(data), None)
        buf.fill(0, data)

        # timestamps
        duration = int(1e9 / float(self.fps))
        buf.duration = duration
        buf.pts = Gst.CLOCK_TIME_NONE
        buf.dts = Gst.CLOCK_TIME_NONE

        self.appsrc.emit("push-buffer", buf)

    # ==================================
    # RED detect
    # ==================================
    def detect_red(self, frame):
        h, w = frame.shape[:2]

        roi_w = int(w * self.roi_ratio_w)
        roi_h = int(h * self.roi_ratio_h)
        x1 = (w - roi_w) // 2
        y1 = (h - roi_h) // 2

        roi = frame[y1 : y1 + roi_h, x1 : x1 + roi_w]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        mask1 = cv2.inRange(hsv, self.lower_red1, self.upper_red1)
        mask2 = cv2.inRange(hsv, self.lower_red2, self.upper_red2)
        mask = cv2.bitwise_or(mask1, mask2)

        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, self.kernel)

        red_area = int(np.sum(mask > 0))
        red_now = red_area >= self.min_red_area

        vis = frame.copy()
        cv2.rectangle(vis, (x1, y1), (x1 + roi_w, y1 + roi_h), (0, 255, 0), 2)

        vis_roi = vis[y1 : y1 + roi_h, x1 : x1 + roi_w]
        overlay = vis_roi.copy()
        overlay[mask > 0] = (0, 0, 255)
        blended = cv2.addWeighted(vis_roi, 0.6, overlay, 0.4, 0.0)
        vis[y1 : y1 + roi_h, x1 : x1 + roi_w] = blended

        return red_now, vis

    # ==================================
    # Main loop
    # ==================================
    def tick(self):
        frame = self.get_frame()
        if frame is None:
            return

        h, w = frame.shape[:2]
        self.ensure_out_pipeline(w, h)

        # -----------------------
        # YOLO
        # -----------------------
        results = self.model.predict(frame, conf=self.conf, verbose=False)

        labels = []
        annotated = frame

        if results and results[0].boxes is not None and len(results[0].boxes) > 0:
            r0 = results[0]
            class_ids = [int(x) for x in r0.boxes.cls.tolist()]
            labels = [
                self.remap_label(str(self.model.names.get(cid, cid)))
                for cid in class_ids
            ]
            annotated = r0.plot()

        out = ",".join(sorted(set(labels)))

        msg = String()
        msg.data = out
        self.pub_labels.publish(msg)

        # -----------------------
        # RED
        # -----------------------
        red_now, _vis = self.detect_red(frame)

        self.red_count = self.red_count + 1 if red_now else 0
        red_filtered = self.red_count >= self.red_needed_frames

        if red_filtered and not self.stop_active:
            self.stop_active = True
            self.get_logger().info("RED -> STOP")

        if not red_filtered and self.stop_active:
            self.stop_active = False
            self.get_logger().info("RED CLEARED")

        self.pub_stop.publish(Bool(data=self.stop_active))

        # -----------------------
        # Overlay text + stream out
        # -----------------------
        display = annotated.copy()
        cv2.putText(
            display,
            f"STOP={self.stop_active} | {out if out else '-'}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
        )

        # ✅ 오버레이 스트림 송출
        self.push_frame(display)

        # -----------------------
        # Preview (optional)
        # -----------------------
        if not self.no_preview:
            cv2.imshow("Unified YOLO + RED STOP", display)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                rclpy.shutdown()

    def destroy_node(self):
        try:
            if self.pipeline_in is not None:
                self.pipeline_in.set_state(Gst.State.NULL)
        except Exception:
            pass

        try:
            if self.pipeline_out is not None:
                self.pipeline_out.set_state(Gst.State.NULL)
        except Exception:
            pass

        try:
            if not self.no_preview:
                cv2.destroyAllWindows()
        except Exception:
            pass

        super().destroy_node()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-port", type=int, default=DEFAULT_IN_PORT)
    ap.add_argument("--out-host", default="192.168.1.8")
    ap.add_argument("--out-port", type=int, default=6000)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bitrate", type=int, default=1200)
    ap.add_argument("--payload", type=int, default=DEFAULT_PAYLOAD)
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--labels-topic", default="/yolo/detection_labels")
    ap.add_argument("--stop-topic", default="/safety/stop")
    ap.add_argument("--node-name", default="unified_yolo_red_stop_pinky1")
    args = ap.parse_args()

    rclpy.init()
    node = UnifiedYoloRedStop(
        in_port=args.in_port,
        out_host=args.out_host,
        out_port=args.out_port,
        fps=args.fps,
        bitrate=args.bitrate,
        payload=args.payload,
        no_preview=args.no_preview,
        labels_topic=args.labels_topic,
        stop_topic=args.stop_topic,
        node_name=args.node_name,
    )
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
