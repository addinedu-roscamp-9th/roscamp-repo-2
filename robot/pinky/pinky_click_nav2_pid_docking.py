#!/usr/bin/env python3
import math
import sys
from math import atan2, sqrt

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient

from geometry_msgs.msg import Twist, PoseStamped
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose
from std_msgs.msg import String
from nav2_msgs.srv import ClearEntireCostmap


# ==========================
#  간단한 PID 클래스
# ==========================
class PID:
    def __init__(self, kp=1.0, ki=0.0, kd=0.0,
                 output_min=None, output_max=None,
                 integral_limit=None):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self.integral_limit = integral_limit

        self.integral = 0.0
        self.prev_error = 0.0
        self.prev_time = None

    def reset(self):
        self.integral = 0.0
        self.prev_error = 0.0
        self.prev_time = None

    def update(self, error, now_sec):
        if self.prev_time is None:
            dt = 0.01
        else:
            dt = now_sec - self.prev_time
            if dt <= 0.0:
                dt = 0.01

        self.integral += error * dt
        if self.integral_limit is not None:
            lim = float(self.integral_limit)
            self.integral = max(min(self.integral, lim), -lim)

        derivative = (error - self.prev_error) / dt

        output = (
            self.kp * error +
            self.ki * self.integral +
            self.kd * derivative
        )

        if self.output_min is not None:
            output = max(float(self.output_min), output)
        if self.output_max is not None:
            output = min(float(self.output_max), output)

        self.prev_error = error
        self.prev_time = now_sec
        return output


# ==========================
#  각도 관련 유틸
# ==========================
def normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def yaw_from_quaternion(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def quat_from_yaw(yaw: float):
    return (math.sin(yaw / 2.0), math.cos(yaw / 2.0))


class PinkyClickNav2PidDocking(Node):
    def __init__(self):
        super().__init__("pinky_click_nav2_pid_docking")

        # 모드: IDLE / NAV2 / PID_POS / PID_FINE_POS / PID_YAW_FINAL / FINISHING
        self.mode = "IDLE"

        # ----- 파라미터 -----
        self.declare_parameter("switch_distance", 0.08)
        self.declare_parameter("pos_distance_tolerance", 0.02)
        self.declare_parameter("fine_distance_tolerance", 0.005)
        self.declare_parameter("final_yaw_tolerance", 0.02)

        self.declare_parameter("P_linear", 1.2)
        self.declare_parameter("I_linear", 0.02)
        self.declare_parameter("D_linear", 0.1)
        self.declare_parameter("max_linear", 0.20)
        self.declare_parameter("min_linear", -0.20)

        self.declare_parameter("P_angular", 2.0)
        self.declare_parameter("I_angular", 0.0)
        self.declare_parameter("D_angular", 0.1)
        self.declare_parameter("max_angular", 0.8)
        self.declare_parameter("min_angular", -0.8)

        # ✅ 소프트 리셋 관련
        self.declare_parameter("soft_reset_stop_repeat", 10)
        self.declare_parameter("soft_reset_stop_dt", 0.05)

        # ✅ costmap entirely clear 관련
        self.declare_parameter("enable_costmap_clear", True)
        self.declare_parameter(
            "costmap_clear_services",
            [
                "/global_costmap/clear_entirely_global_costmap",
                "/local_costmap/clear_entirely_local_costmap",
            ],
        )

        # ✅ 완료 후 자동 종료(= Ctrl+C 효과)
        self.declare_parameter("auto_exit_on_complete", True)
        self.declare_parameter("exit_delay_sec", 0.2)

        # ✅ ZONE 3개: "goal_x 구간 -> via 1개" 규칙
        self.declare_parameter("enable_zone_via", True)

        # ZONE_A
        self.declare_parameter("zone_a_min_x", 0.450)
        self.declare_parameter("zone_a_max_x", 0.950)
        self.declare_parameter("zone_a_via_x", 0.730)
        self.declare_parameter("zone_a_via_y", -0.010)

        # ZONE_B
        self.declare_parameter("zone_b_min_x", 0.951)
        self.declare_parameter("zone_b_max_x", 1.400)
        self.declare_parameter("zone_b_via_x", 1.230)
        self.declare_parameter("zone_b_via_y", -0.010)

        # ZONE_C
        self.declare_parameter("zone_c_min_x", 1.401)
        self.declare_parameter("zone_c_max_x", 1.700)
        self.declare_parameter("zone_c_via_x", 1.560)
        self.declare_parameter("zone_c_via_y", -0.010)

        # via 도달 판정(중간점 한 번 거친 뒤 goal로 넘어감)
        self.declare_parameter("via_reach_tolerance", 0.05)

        # ✅ via 도착 후 안정화(텀) 추가
        self.declare_parameter("waypoint_settle_sec", 0.5)
        self.declare_parameter("waypoint_stop_repeat", 15)

        # ---- load ----
        self.switch_distance = float(self.get_parameter("switch_distance").value)
        self.pos_distance_tolerance = float(self.get_parameter("pos_distance_tolerance").value)
        self.fine_distance_tolerance = float(self.get_parameter("fine_distance_tolerance").value)
        self.final_yaw_tolerance = float(self.get_parameter("final_yaw_tolerance").value)

        P_lin = float(self.get_parameter("P_linear").value)
        I_lin = float(self.get_parameter("I_linear").value)
        D_lin = float(self.get_parameter("D_linear").value)
        max_lin = float(self.get_parameter("max_linear").value)
        min_lin = float(self.get_parameter("min_linear").value)

        P_ang = float(self.get_parameter("P_angular").value)
        I_ang = float(self.get_parameter("I_angular").value)
        D_ang = float(self.get_parameter("D_angular").value)
        max_ang = float(self.get_parameter("max_angular").value)
        min_ang = float(self.get_parameter("min_angular").value)

        self.soft_reset_stop_repeat = int(self.get_parameter("soft_reset_stop_repeat").value)
        self.soft_reset_stop_dt = float(self.get_parameter("soft_reset_stop_dt").value)

        self.enable_costmap_clear = bool(self.get_parameter("enable_costmap_clear").value)
        self.costmap_clear_services = list(self.get_parameter("costmap_clear_services").value)

        self.auto_exit_on_complete = bool(self.get_parameter("auto_exit_on_complete").value)
        self.exit_delay_sec = float(self.get_parameter("exit_delay_sec").value)

        self.enable_zone_via = bool(self.get_parameter("enable_zone_via").value)

        self.zone_a_min_x = float(self.get_parameter("zone_a_min_x").value)
        self.zone_a_max_x = float(self.get_parameter("zone_a_max_x").value)
        self.zone_a_via = (
            float(self.get_parameter("zone_a_via_x").value),
            float(self.get_parameter("zone_a_via_y").value),
        )

        self.zone_b_min_x = float(self.get_parameter("zone_b_min_x").value)
        self.zone_b_max_x = float(self.get_parameter("zone_b_max_x").value)
        self.zone_b_via = (
            float(self.get_parameter("zone_b_via_x").value),
            float(self.get_parameter("zone_b_via_y").value),
        )

        self.zone_c_min_x = float(self.get_parameter("zone_c_min_x").value)
        self.zone_c_max_x = float(self.get_parameter("zone_c_max_x").value)
        self.zone_c_via = (
            float(self.get_parameter("zone_c_via_x").value),
            float(self.get_parameter("zone_c_via_y").value),
        )

        self.via_reach_tolerance = float(self.get_parameter("via_reach_tolerance").value)

        self.waypoint_settle_sec = float(self.get_parameter("waypoint_settle_sec").value)
        self.waypoint_stop_repeat = int(self.get_parameter("waypoint_stop_repeat").value)

        # PID 객체
        self.linear_pid = PID(
            kp=P_lin, ki=I_lin, kd=D_lin,
            output_min=min_lin, output_max=max_lin,
            integral_limit=1.0
        )
        self.angular_pid = PID(
            kp=P_ang, ki=I_ang, kd=D_ang,
            output_min=min_ang, output_max=max_ang,
            integral_limit=1.0
        )

        # 상태
        self.current_pose = None

        # ✅ (via -> goal) 시퀀스
        self.seq = []
        self.active_target = None  # (x, y, is_final)
        self.final_goal_yaw = None

        # ✅ via 도착 후 안정화 타이머
        self._waypoint_settle_until = None

        self.nav2_goal_handle = None

        # Nav2 액션 클라이언트
        self.nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")

        # 구독/발행
        self.pose_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self.amcl_callback, 10
        )
        self.goal_sub = self.create_subscription(
            PoseStamped, "/click_goal", self.goal_callback, 10
        )

        self.cmd_vel_docking_pub = self.create_publisher(Twist, "/cmd_vel_docking", 10)
        self.docking_status_pub = self.create_publisher(String, "/docking_status", 10)

        # 제어 루프
        self.control_timer = self.create_timer(0.05, self.control_loop)

        # 종료 예약 타이머 핸들(중복 방지)
        self._exit_timer = None

        self.get_logger().info(
            "PinkyClickNav2PidDocking started. "
            "(ZONE 3 + settle, via=Nav2 only, z-flag optional: z==0 treated as FINAL for UI compatibility)"
        )

    # ==========================
    # ZONE 3개 매칭: goal_x 구간이면 via 1개 리턴
    # ==========================
    def _match_zone_via(self, goal_x: float):
        if not self.enable_zone_via:
            return (None, None)

        x = float(goal_x)
        if self.zone_a_min_x <= x <= self.zone_a_max_x:
            return ("ZONE_A", self.zone_a_via)
        if self.zone_b_min_x <= x <= self.zone_b_max_x:
            return ("ZONE_B", self.zone_b_via)
        if self.zone_c_min_x <= x <= self.zone_c_max_x:
            return ("ZONE_C", self.zone_c_via)
        return (None, None)

    def _build_sequence(self, gx: float, gy: float, is_final: bool):
        self.seq.clear()
        self.active_target = None

        # final이면: 매칭되는 zone이 있으면 via 1개 추가
        zone, via = self._match_zone_via(gx) if is_final else (None, None)
        if is_final and via is not None:
            self.get_logger().info(
                f"[RULE] {zone}: goal_x={gx:.3f} -> via=({via[0]:.3f},{via[1]:.3f})"
            )
            self.seq.append((float(via[0]), float(via[1]), False))

        # 마지막에 최종 goal (is_final True면 final, False면 단독 waypoint)
        self.seq.append((float(gx), float(gy), bool(is_final)))
        self.active_target = self.seq[0]

    def _advance_target(self) -> bool:
        if not self.seq:
            self.active_target = None
            return False
        self.seq.pop(0)
        if not self.seq:
            self.active_target = None
            return False
        self.active_target = self.seq[0]
        self.get_logger().info(f"[SEQ] next -> {self.active_target} (remain {len(self.seq)})")
        return True

    # ========== 콜백들 ==========

    def amcl_callback(self, msg: PoseWithCovarianceStamped):
        pose = msg.pose.pose
        x = float(pose.position.x)
        y = float(pose.position.y)
        yaw = float(yaw_from_quaternion(pose.orientation))
        self.current_pose = (x, y, yaw)

    def goal_callback(self, msg: PoseStamped):
        if self.mode == "FINISHING":
            return

        gx = float(msg.pose.position.x)
        gy = float(msg.pose.position.y)

        # ==========================
        # ✅ 핵심 수정(= UI 수정 없이 기존 동작 유지):
        # - z가 0이면 FINAL로 간주 (RViz/기존 UI 호환)
        # - z를 쓰는 UI라면: z>=0.5 FINAL, z<0.5 WAYPOINT 단독도 가능
        # ==========================
        z_flag = float(msg.pose.position.z)
        if abs(z_flag) < 1e-9:
            is_final = True
        else:
            is_final = (z_flag >= 0.5)

        incoming_yaw = float(yaw_from_quaternion(msg.pose.orientation))

        # final yaw는 final goal에만 적용
        self.final_goal_yaw = incoming_yaw if is_final else None

        # ✅ 시퀀스 구성 (via -> goal) 또는 단독 waypoint
        self._build_sequence(gx, gy, is_final)

        # ✅ settle 상태 초기화
        self._waypoint_settle_until = None

        self.linear_pid.reset()
        self.angular_pid.reset()

        self.get_logger().info(
            f"New /click_goal: goal=({gx:.3f},{gy:.3f}) yaw={incoming_yaw:.3f} "
            f"z={z_flag:.3f} is_final={is_final} seq={self.seq}"
        )

        # 기존 goal 취소(완료 대기)
        if self.nav2_goal_handle is not None:
            try:
                cancel_future = self.nav2_goal_handle.cancel_goal_async()
                rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=1.0)
            except Exception as e:
                self.get_logger().warn(f"Failed to cancel previous Nav2 goal: {e}")
            self.nav2_goal_handle = None

        # 첫 타깃 전송
        self._send_active_target_nav2()

    # ========== Nav2 액션 관련 ==========

    def _send_active_target_nav2(self):
        if self.active_target is None:
            return

        if not self.nav_client.wait_for_server(timeout_sec=1.0):
            self.get_logger().warn("Nav2 action server not available.")
            return

        x, y, _ = self.active_target

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = PoseStamped()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = float(x)
        goal_msg.pose.pose.position.y = float(y)

        # Nav2에는 yaw=0으로 (주행 중 회전 억제)
        qz, qw = quat_from_yaw(0.0)
        goal_msg.pose.pose.orientation.z = qz
        goal_msg.pose.pose.orientation.w = qw

        send_future = self.nav_client.send_goal_async(goal_msg, feedback_callback=self.nav2_feedback_cb)
        send_future.add_done_callback(self.nav2_goal_response_cb)
        self.mode = "NAV2"
        self.get_logger().info(f"[NAV2] Goal sent: x={x:.3f}, y={y:.3f}, yaw=0.0")

    def nav2_feedback_cb(self, feedback_msg):
        pass

    def nav2_goal_response_cb(self, future):
        try:
            goal_handle = future.result()
        except Exception as e:
            self.get_logger().error(f"[NAV2] Goal send failed: {e}")
            self.nav2_goal_handle = None
            self.mode = "IDLE"
            return

        if not goal_handle.accepted:
            self.get_logger().warn("[NAV2] Goal rejected.")
            self.nav2_goal_handle = None
            self.mode = "IDLE"
            return

        self.get_logger().info("[NAV2] Goal accepted.")
        self.nav2_goal_handle = goal_handle

        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self.nav2_result_cb)

    def nav2_result_cb(self, future):
        try:
            result = future.result()
            status = result.status
            self.get_logger().info(f"[NAV2] Result received. status={status}")
        except Exception as e:
            self.get_logger().warn(f"[NAV2] Result callback error: {e}")

    def publish_docking_status(self, text: str):
        msg = String()
        msg.data = text
        self.docking_status_pub.publish(msg)

    # ========== “브링업 다시 킨 듯” 소프트 리셋 ==========

    def soft_reset_nav2_like_rebringup(self, reason: str = ""):
        self.get_logger().warn(f"[SOFT_RESET] start. reason={reason}")

        # 1) Nav2 goal cancel(완료까지 대기)
        if self.nav2_goal_handle is not None:
            try:
                cancel_future = self.nav2_goal_handle.cancel_goal_async()
                rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=1.0)
            except Exception as e:
                self.get_logger().warn(f"[SOFT_RESET] cancel_goal_async failed: {e}")

        # 2) stop 반복 전송
        repeat = max(1, int(self.soft_reset_stop_repeat))
        dt = max(0.0, float(self.soft_reset_stop_dt))
        for _ in range(repeat):
            self.publish_stop()
            rclpy.spin_once(self, timeout_sec=dt if dt > 0.0 else 0.02)

        # 3) 내부 상태 완전 초기화
        self.nav2_goal_handle = None
        self.seq.clear()
        self.active_target = None
        self.final_goal_yaw = None
        self._waypoint_settle_until = None

        self.mode = "IDLE"
        self.linear_pid.reset()
        self.angular_pid.reset()

        # 4) costmap entirely clear (선택)
        if self.enable_costmap_clear:
            self._clear_costmaps_entirely()

        self.publish_stop()
        self.get_logger().warn("[SOFT_RESET] done.")

    def _clear_costmaps_entirely(self):
        if not self.costmap_clear_services:
            self.get_logger().warn("[SOFT_RESET] costmap_clear_services empty. skip.")
            return

        for svc_name in self.costmap_clear_services:
            try:
                client = self.create_client(ClearEntireCostmap, svc_name)
                if not client.wait_for_service(timeout_sec=0.5):
                    self.get_logger().warn(f"[SOFT_RESET] costmap clear svc not available: {svc_name}")
                    self.destroy_client(client)
                    continue

                req = ClearEntireCostmap.Request()
                fut = client.call_async(req)
                rclpy.spin_until_future_complete(self, fut, timeout_sec=1.0)
                self.get_logger().info(f"[SOFT_RESET] costmap cleared entirely: {svc_name}")
                self.destroy_client(client)
            except Exception as e:
                self.get_logger().warn(f"[SOFT_RESET] costmap clear failed {svc_name}: {e}")

    # ========== 완료 처리(주행완료 출력 + 마지막에 종료) ==========

    def finish_run(self, reason: str):
        if self.mode == "FINISHING":
            return

        self.mode = "FINISHING"

        # 리셋
        self.soft_reset_nav2_like_rebringup(reason=reason)

        # 완료 메시지
        self.publish_docking_status("주행완료")
        self.get_logger().info("주행완료")

        # 가장 마지막: 자동 종료
        if self.auto_exit_on_complete:
            self._schedule_shutdown()

    def _schedule_shutdown(self):
        if self._exit_timer is not None:
            return

        delay = max(0.0, float(self.exit_delay_sec))

        def _do_shutdown():
            try:
                self.publish_stop()
            except Exception:
                pass
            rclpy.shutdown()
            try:
                sys.exit(0)
            except SystemExit:
                pass

        self._exit_timer = self.create_timer(delay, _do_shutdown)

    # ========== 제어 루프 ==========

    def control_loop(self):
        if self.mode == "FINISHING":
            self.publish_stop()
            return

        # ✅ via 도착 후 안정화 대기 처리
        if self._waypoint_settle_until is not None:
            now_sec = self.get_clock().now().nanoseconds * 1e-9
            if now_sec < self._waypoint_settle_until:
                self.publish_stop()
                return
            # 대기 끝 -> 다음 goal 전송
            self._waypoint_settle_until = None
            self.linear_pid.reset()
            self.angular_pid.reset()
            self._send_active_target_nav2()
            return

        if self.current_pose is None or self.active_target is None:
            self.publish_stop()
            return

        x, y, yaw = self.current_pose
        tx, ty, is_final = self.active_target

        dx = tx - x
        dy = ty - y
        distance = sqrt(dx * dx + dy * dy)
        now_sec = self.get_clock().now().nanoseconds * 1e-9

        # ---- NAV2 모드 ----
        if self.mode == "NAV2":
            # ✅ via는 Nav2 ONLY (PID 전환 금지)
            if not is_final:
                if distance < self.via_reach_tolerance:
                    self.get_logger().info(
                        f"[VIA-NAV2] reached by Nav2. dist={distance:.3f} < {self.via_reach_tolerance:.3f}"
                    )

                    # via goal 정리
                    if self.nav2_goal_handle is not None:
                        try:
                            cancel_future = self.nav2_goal_handle.cancel_goal_async()
                            rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=1.0)
                        except Exception as e:
                            self.get_logger().warn(f"[VIA-NAV2] cancel failed: {e}")
                        self.nav2_goal_handle = None

                    # stop 여러 번
                    for _ in range(max(1, self.waypoint_stop_repeat)):
                        self.publish_stop()

                    # 다음 타깃으로 이동 (대기 후 Nav2 전송)
                    if self._advance_target():
                        now_sec2 = self.get_clock().now().nanoseconds * 1e-9
                        self._waypoint_settle_until = now_sec2 + max(0.0, self.waypoint_settle_sec)
                        self.mode = "IDLE"
                    else:
                        self.finish_run(reason="via_only_complete")
                return

            # ✅ final: 일정 거리 들어오면 Nav2 취소 후 PID로 전환
            if distance < self.switch_distance:
                self.get_logger().info(f"[NAV2→PID_POS] dist={distance:.3f} < {self.switch_distance:.3f}")

                if self.nav2_goal_handle is not None:
                    try:
                        cancel_future = self.nav2_goal_handle.cancel_goal_async()
                        rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=1.0)
                    except Exception as e:
                        self.get_logger().warn(f"Failed to cancel Nav2 goal: {e}")
                    self.nav2_goal_handle = None

                self.publish_stop()
                self.linear_pid.reset()
                self.angular_pid.reset()
                self.mode = "PID_POS"
            return

        # ---- PID_POS ----
        if self.mode == "PID_POS":
            desired_heading = atan2(dy, dx)
            heading_error = normalize_angle(desired_heading - yaw)

            v_cmd = self.linear_pid.update(distance, now_sec)
            w_cmd = self.angular_pid.update(heading_error, now_sec)

            if abs(heading_error) > 0.7:
                v_cmd = 0.0

            if (distance < 0.02) and (abs(heading_error) < 0.4):
                v_dead = 0.02
                v_kick = 0.02
                if 0.0 < v_cmd < v_dead:
                    v_cmd = v_kick

            self.publish_cmd(v_cmd, w_cmd)

            if distance < self.pos_distance_tolerance:
                self.get_logger().info(f"[PID_POS→PID_FINE_POS] dist={distance:.3f}")
                self.publish_stop()
                self.linear_pid.reset()
                self.angular_pid.reset()
                self.mode = "PID_FINE_POS"
            return

        # ---- PID_FINE_POS ----
        if self.mode == "PID_FINE_POS":
            desired_heading = atan2(dy, dx)
            heading_error = normalize_angle(desired_heading - yaw)

            if distance < self.fine_distance_tolerance:
                self.get_logger().info(f"[PID_FINE_POS→PID_YAW_FINAL] dist={distance:.4f}")
                self.publish_stop()
                self.angular_pid.reset()
                self.mode = "PID_YAW_FINAL"
                return

            w_cmd = self.angular_pid.update(heading_error, now_sec)
            if abs(heading_error) > 0.5:
                v_cmd = 0.0
            else:
                v_cmd = self.linear_pid.update(distance, now_sec)

            v_cmd *= 0.5
            w_cmd *= 0.5

            if abs(v_cmd) < 0.003:
                v_cmd = 0.0
            if abs(w_cmd) < 0.01:
                w_cmd = 0.0

            self.publish_cmd(v_cmd, w_cmd)
            return

        # ---- PID_YAW_FINAL ----
        if self.mode == "PID_YAW_FINAL":
            if self.final_goal_yaw is None:
                self.get_logger().error("[FINAL] final_goal_yaw is None (should never happen).")
                self.finish_run(reason="missing_final_yaw")
                return

            yaw_error = normalize_angle(float(self.final_goal_yaw) - float(yaw))

            if abs(yaw_error) < self.final_yaw_tolerance:
                self.get_logger().info(
                    f"[FINAL] yaw aligned. desired={float(self.final_goal_yaw):.3f}, err={yaw_error:.4f}"
                )
                self.publish_docking_status("DOCKING_COMPLETE")
                self.finish_run(reason="final_complete")
                return

            w_cmd = self.angular_pid.update(yaw_error, now_sec)
            if abs(w_cmd) < 0.01:
                w_cmd = 0.0
            self.publish_cmd(0.0, w_cmd)
            return

        self.publish_stop()

    def publish_cmd(self, v: float, w: float):
        msg = Twist()
        msg.linear.x = float(v)
        msg.angular.z = float(w)
        self.cmd_vel_docking_pub.publish(msg)

    def publish_stop(self):
        self.publish_cmd(0.0, 0.0)


def main(args=None):
    rclpy.init(args=args)
    node = PinkyClickNav2PidDocking()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Interrupted by user.")
    finally:
        try:
            node.soft_reset_nav2_like_rebringup(reason="shutdown")
        except Exception:
            pass
        try:
            node.publish_stop()
        except Exception:
            pass
        node.destroy_node()
        try:
            if rclpy.ok():
                rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
