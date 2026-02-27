#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import rclpy
from rclpy.node import Node
from rclpy.duration import Duration

from std_msgs.msg import String, Bool
from geometry_msgs.msg import Twist


class CmdVelArbiterYolo(Node):
    def __init__(self):
        super().__init__("cmd_vel_arbiter_yolo")

        # in/out topics
        self.declare_parameter("nav_cmd_topic", "/cmd_vel_nav")
        self.declare_parameter("docking_cmd_topic", "/cmd_vel_docking")
        self.declare_parameter("cmd_out_topic", "/cmd_vel")
        self.declare_parameter("detection_topic", "/yolo/detection_labels")

        # === ADD: external STOP override input (RED, safety, etc.) ===
        self.declare_parameter("stop_topic", "/safety/stop")
        self.declare_parameter("stop_timeout_sec", 0.6)  # stop 신호 끊김 완화(최근 True면 잠깐 유지)

        # YOLO policy
        self.declare_parameter("wait_pinky_sec", 2.0)
        self.declare_parameter("human_timeout_sec", 0.6)

        # Priority timing
        self.declare_parameter("docking_timeout_sec", 0.6)  # 최근 도킹 cmd면 도킹 우선(단, 0이면 무시)
        self.declare_parameter("pub_hz", 20.0)

        self.nav_cmd_topic = self.get_parameter("nav_cmd_topic").value
        self.docking_cmd_topic = self.get_parameter("docking_cmd_topic").value
        self.cmd_out_topic = self.get_parameter("cmd_out_topic").value
        self.detection_topic = self.get_parameter("detection_topic").value

        self.stop_topic = self.get_parameter("stop_topic").value
        self.stop_timeout_sec = float(self.get_parameter("stop_timeout_sec").value)

        self.wait_pinky_sec = float(self.get_parameter("wait_pinky_sec").value)
        self.human_timeout_sec = float(self.get_parameter("human_timeout_sec").value)
        self.docking_timeout_sec = float(self.get_parameter("docking_timeout_sec").value)
        self.pub_hz = float(self.get_parameter("pub_hz").value)

        # state (기존)
        self.last_human_seen = None
        self.pinky_hold_until = None

        self.last_nav = Twist()
        self.last_docking = Twist()

        self.last_nav_t = None
        self.last_docking_t = None

        # state (추가: STOP)
        self.stop_cmd = False          # 마지막 stop 값
        self.last_stop_t = None        # 마지막 stop 수신 시각

        # subs
        self.create_subscription(String, self.detection_topic, self.on_det, 10)
        self.create_subscription(Twist, self.nav_cmd_topic, self.on_nav, 10)
        self.create_subscription(Twist, self.docking_cmd_topic, self.on_docking, 10)

        # sub (추가)
        self.create_subscription(Bool, self.stop_topic, self.on_stop, 10)

        # pub
        self.pub = self.create_publisher(Twist, self.cmd_out_topic, 10)

        period = 1.0 / max(self.pub_hz, 1.0)
        self.create_timer(period, self.tick)

        self.get_logger().info(f"nav in: {self.nav_cmd_topic}")
        self.get_logger().info(f"docking in: {self.docking_cmd_topic}")
        self.get_logger().info(f"out: {self.cmd_out_topic}")
        self.get_logger().info(
            f"yolo: {self.detection_topic} (human hold, pinky_63 {self.wait_pinky_sec}s)"
        )
        self.get_logger().info(f"stop in: {self.stop_topic} (timeout={self.stop_timeout_sec}s)")

    def _parse_labels(self, s: str):
        s = (s or "").strip()
        if not s:
            return set()
        return {x.strip() for x in s.split(",") if x.strip()}

    def on_det(self, msg: String):
        labels = self._parse_labels(msg.data)
        now = self.get_clock().now()

        if "human" in labels:
            self.last_human_seen = now
        if "pinky_63" in labels:
            self.pinky_hold_until = now + Duration(seconds=self.wait_pinky_sec)

    def on_stop(self, msg: Bool):
        self.stop_cmd = bool(msg.data)
        self.last_stop_t = self.get_clock().now()

    def on_nav(self, msg: Twist):
        self.last_nav = msg
        self.last_nav_t = self.get_clock().now()

    def on_docking(self, msg: Twist):
        self.last_docking = msg
        self.last_docking_t = self.get_clock().now()

    def _recent(self, t, timeout_sec: float):
        if t is None:
            return False
        now = self.get_clock().now()
        dt = (now - t).nanoseconds * 1e-9
        return dt <= timeout_sec

    def _pause_active(self):
        now = self.get_clock().now()

        human_active = False
        if self.last_human_seen is not None:
            dt = (now - self.last_human_seen).nanoseconds * 1e-9
            human_active = (dt <= self.human_timeout_sec)

        pinky_active = (self.pinky_hold_until is not None and now < self.pinky_hold_until)
        return human_active or pinky_active

    def _stop_active(self) -> bool:
        # stop_cmd가 True이고, stop 메시지가 최근에 들어왔다면 stop 유지
        if not self.stop_cmd:
            return False
        return self._recent(self.last_stop_t, self.stop_timeout_sec)

    def _is_zero(self, t: Twist, eps: float = 1e-4) -> bool:
        return (abs(t.linear.x) < eps and abs(t.linear.y) < eps and abs(t.linear.z) < eps and
                abs(t.angular.x) < eps and abs(t.angular.y) < eps and abs(t.angular.z) < eps)

    def tick(self):
        # === 0) ADD: 외부 STOP(빨강 등) 최우선 ===
        if self._stop_active():
            self.pub.publish(Twist())
            return

        # 1) 기존: YOLO pause 최우선
        if self._pause_active():
            self.pub.publish(Twist())
            return

        docking_recent = self._recent(self.last_docking_t, self.docking_timeout_sec)
        docking_nonzero = (not self._is_zero(self.last_docking))

        # 2) 기존: 우선순위 "도킹 최근 + 0이 아님"이면 도킹, 아니면 nav2
        if docking_recent and docking_nonzero:
            self.pub.publish(self.last_docking)
        else:
            self.pub.publish(self.last_nav)


def main():
    rclpy.init()
    node = CmdVelArbiterYolo()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
