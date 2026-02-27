#!/usr/bin/env python3
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Int32

from pinkylib import Battery


# 6.80V = 0%, 8.80V 이상 = 100%
def voltage_to_percent(v: float) -> int:
    v_empty = 6.80
    v_full = 8.80
    p = (v - v_empty) / (v_full - v_empty) * 100.0
    p = max(0.0, min(100.0, p))
    return int(round(p))


class BatteryPercentPublisher(Node):
    def __init__(self):
        super().__init__("battery_percent_publisher")
        self.pub = self.create_publisher(Int32, "/battery_percent", 10)
        self.battery = Battery()

        # 1Hz 권장 (너무 자주 읽을 필요 없음)
        self.timer = self.create_timer(1.0, self.tick)

    def tick(self):
        try:
            v = float(self.battery.get_voltage())
            pct = voltage_to_percent(v)

            msg = Int32()
            msg.data = pct
            self.pub.publish(msg)

            # 로그는 필요하면 남기고, 시끄러우면 주석처리
            self.get_logger().info(f"battery: {pct}% ({v:.2f}V)")
        except Exception as e:
            self.get_logger().warn(f"battery read failed: {e}")


def main():
    rclpy.init()
    node = BatteryPercentPublisher()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
