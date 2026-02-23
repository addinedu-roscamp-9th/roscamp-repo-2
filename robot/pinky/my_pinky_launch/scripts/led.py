#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import signal

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from pinky_interfaces.srv import SetLed


led_process = None  # 전역으로 LED 서버 프로세스 보관


def ensure_led_server():
    global led_process

    try:
        out = subprocess.check_output(["ros2", "service", "list"], text=True)
        if "/set_led" in out:
            return
    except Exception as e:
        print(f"[WARN] ros2 service list 실패: {e}", file=sys.stderr)

    print("[INFO] /set_led not found -> starting led_server")
    led_process = subprocess.Popen(
        ["ros2", "run", "pinky_led", "led_server"],
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)
    )

    # 서비스 뜰 때까지 대기
    for _ in range(50):
        try:
            out = subprocess.check_output(["ros2", "service", "list"], text=True)
            if "/set_led" in out:
                print("[INFO] led_server started successfully")
                return
        except Exception:
            pass
        time.sleep(0.1)

    print("[WARN] led_server 실행했지만 /set_led 확인 실패")


class SafetyLedController(Node):
    def __init__(self):
        super().__init__("safety_led_controller")

        self.create_subscription(Bool, "/safety/stop", self.on_stop, 10)

        self.led_cli = self.create_client(SetLed, "/set_led")
        while not self.led_cli.wait_for_service(timeout_sec=1.0):
            self.get_logger().info("Waiting for /set_led service...")

        self.last_stop = None
        self.apply_state(False)  # 디폴트 초록

    def on_stop(self, msg: Bool):
        if self.last_stop is not None and msg.data == self.last_stop:
            return
        self.apply_state(msg.data)

    def apply_state(self, stop: bool):
        self.last_stop = stop
        if stop:
            self.set_fill(255, 0, 0)
        else:
            self.set_fill(0, 255, 0)

    def set_fill(self, r: int, g: int, b: int):
        req = SetLed.Request()
        req.command = "fill"
        req.r = int(r)
        req.g = int(g)
        req.b = int(b)
        self.led_cli.call_async(req)


def main():
    global led_process

    ensure_led_server()

    rclpy.init()
    node = SafetyLedController()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C received, shutting down...")

    node.destroy_node()
    rclpy.shutdown()

    # LED 서버도 같이 종료
    if led_process is not None:
        print("[INFO] Terminating led_server...")
        led_process.terminate()
        led_process.wait()


if __name__ == "__main__":
    main()
