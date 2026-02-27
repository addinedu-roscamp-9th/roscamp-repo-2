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
from nav2_msgs.srv import ClearEntireCostmap
from std_msgs.msg import String


# ==========================
# ✅ 하드코딩 목표 설정
# ==========================
HARDCODE_GOAL_X = 0.000
HARDCODE_GOAL_Y = 0.000

# ✅ (기존) 웨이포인트
HARDCODE_WAYPOINT_X = -0.025
HARDCODE_WAYPOINT_Y = 0.308

# ✅ (신규) "이 과정들보다 먼저 들렸다 가는" 웨이포인트 (기본값)
HARDCODE_PRE_WAYPOINT_X = 0.400
HARDCODE_PRE_WAYPOINT_Y = 0.715

NAV2_GOAL_YAW = 0.0
FINAL_YAW_CAMERA_DOWN = 0.0


# ==========================
#  PID 클래스
# ==========================
class PID:
    def __init__(self, kp=1.0, ki=0.0, kd=0.0,
                 output_min=None, output_max=None,
                 integral_limit=None):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
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
        dt = 0.01 if self.prev_time is None else (now_sec - self.prev_time)
        if dt <= 0.0:
            dt = 0.01

        self.integral += error * dt
        if self.integral_limit is not None:
            lim = float(self.integral_limit)
            self.integral = max(min(self.integral, lim), -lim)

        derivative = (error - self.prev_error) / dt
        output = self.kp * error + self.ki * self.integral + self.kd * derivative

        if self.output_min is not None:
            output = max(float(self.output_min), output)
        if self.output_max is not None:
            output = min(float(self.output_max), output)

        self.prev_error = error
        self.prev_time = now_sec
        return output


def normalize_angle(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def yaw_from_quaternion(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


class PinkyChargeClean(Node):
    """
    ✅ 운영 방식(킬/재기동 제거 버전)
    - (신규) PRE_WP 구간: Nav2만 사용 (/cmd_vel 건드리지 않음)
    - (기존) WP 구간: Nav2만 사용 (/cmd_vel 건드리지 않음)
    - 웨이포인트 tol 만족: Nav2 goal 확실히 cancel(완료까지 대기) -> settle -> 다음 단계
    - 최종 근접: Nav2 goal 확실히 cancel(완료까지 대기) -> PID로 마무리 (/cmd_vel 사용 시작)
    - ✅ final_yaw_aligned 완료되면: cmd_vel=0을 3초 동안 10번
    - ✅ 종료 직전: Nav2 kill/restart 없이 "소프트 리셋(취소+costmap clear+reset)"만 수행

    ✅ (추가된 기능: pinky_charge.py와 동일 매커니즘 / 동일 수치)
    - 시작할 때 AMCL current_x를 1회만 확인
    - current_x가 ZONE_A/B/C 구간이면, 해당 zone의 via를 "pre-waypoint 1개"로 먼저 방문
    - 구간에 안 걸리면 pre-waypoint는 스킵하고 기존 흐름(WP 또는 FINAL)로 시작
    """

    def __init__(self):
        super().__init__("pinky_charge_clean")

        # 단계
        # PRE_WP_NAV2 -> PRE_WP_SETTLE -> WP_NAV2 -> WP_SETTLE -> FINAL_NAV2 -> PID_POS -> PID_FINE -> PID_YAW -> FINISH
        self.stage = "PRE_WP_NAV2"

        # ----- 파라미터 -----
        self.declare_parameter("nav2_wait_timeout_sec", 0.5)
        self.declare_parameter("nav2_retry_period_sec", 0.2)

        # ✅ (신규) pre-waypoint
        self.declare_parameter("enable_pre_waypoint", True)
        self.declare_parameter("pre_waypoint_x", float(HARDCODE_PRE_WAYPOINT_X))
        self.declare_parameter("pre_waypoint_y", float(HARDCODE_PRE_WAYPOINT_Y))
        self.declare_parameter("pre_waypoint_reach_tolerance", 0.15)
        self.declare_parameter("pre_waypoint_settle_sec", 0.5)

        # (기존) waypoint
        self.declare_parameter("enable_waypoint", True)
        self.declare_parameter("waypoint_x", float(HARDCODE_WAYPOINT_X))
        self.declare_parameter("waypoint_y", float(HARDCODE_WAYPOINT_Y))
        self.declare_parameter("waypoint_reach_tolerance", 0.15)
        self.declare_parameter("waypoint_settle_sec", 0.5)

        # ====== (ADD) ZONE via params (same numbers as pinky_charge.py) ======
        # ZONE_A
        self.declare_parameter("zone_a_min_x", 0.450)
        self.declare_parameter("zone_a_max_x", 0.950)
        self.declare_parameter("zone_a_via_x", 0.730)
        self.declare_parameter("zone_a_via_y", 0.715)

        # ZONE_B
        self.declare_parameter("zone_b_min_x", 0.951)
        self.declare_parameter("zone_b_max_x", 1.400)
        self.declare_parameter("zone_b_via_x", 1.230)
        self.declare_parameter("zone_b_via_y", 0.715)

        # ZONE_C
        self.declare_parameter("zone_c_min_x", 1.401)
        self.declare_parameter("zone_c_max_x", 1.700)
        self.declare_parameter("zone_c_via_x", 1.560)
        self.declare_parameter("zone_c_via_y", 0.715)
        # ================================================================

        # final
        self.declare_parameter("goal_x", float(HARDCODE_GOAL_X))
        self.declare_parameter("goal_y", float(HARDCODE_GOAL_Y))

        # Nav2 -> PID 전환 거리
        self.declare_parameter("switch_distance", 0.08)

        # PID tolerances
        self.declare_parameter("pos_distance_tolerance", 0.02)
        self.declare_parameter("fine_distance_tolerance", 0.005)
        self.declare_parameter("final_yaw_tolerance", 0.02)

        # PID gains
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

        # 완료 후 자동 종료
        self.declare_parameter("auto_exit_on_complete", True)
        self.declare_parameter("exit_delay_sec", 0.2)

        # ✅ final_yaw_aligned 후 cmd_vel 0을 3초 동안 10번
        self.declare_parameter("final_stop_total_sec", 3.0)
        self.declare_parameter("final_stop_count", 10)

        # ✅ 소프트 리셋(브링업 다시 킨 듯) = cancel + stop + costmap clear + 내부 reset
        self.declare_parameter("soft_reset_stop_repeat", 10)
        self.declare_parameter("soft_reset_stop_dt", 0.05)
        self.declare_parameter("enable_costmap_clear", True)
        self.declare_parameter(
            "costmap_clear_services",
            [
                "/global_costmap/clear_entirely_global_costmap",
                "/local_costmap/clear_entirely_local_costmap",
            ],
        )

        # ----- load -----
        self.nav2_wait_timeout_sec = float(self.get_parameter("nav2_wait_timeout_sec").value)
        self.nav2_retry_period_sec = float(self.get_parameter("nav2_retry_period_sec").value)

        # ✅ pre-waypoint load
        self.enable_pre_waypoint = bool(self.get_parameter("enable_pre_waypoint").value)
        self.pre_waypoint_x = float(self.get_parameter("pre_waypoint_x").value)
        self.pre_waypoint_y = float(self.get_parameter("pre_waypoint_y").value)
        self.pre_waypoint_reach_tolerance = float(self.get_parameter("pre_waypoint_reach_tolerance").value)
        self.pre_waypoint_settle_sec = float(self.get_parameter("pre_waypoint_settle_sec").value)

        # (기존) waypoint load
        self.enable_waypoint = bool(self.get_parameter("enable_waypoint").value)
        self.waypoint_x = float(self.get_parameter("waypoint_x").value)
        self.waypoint_y = float(self.get_parameter("waypoint_y").value)
        self.waypoint_reach_tolerance = float(self.get_parameter("waypoint_reach_tolerance").value)
        self.waypoint_settle_sec = float(self.get_parameter("waypoint_settle_sec").value)

        # ====== (ADD) ZONE param load ======
        self.zone_a_min_x = float(self.get_parameter("zone_a_min_x").value)
        self.zone_a_max_x = float(self.get_parameter("zone_a_max_x").value)
        self.zone_a_via_x = float(self.get_parameter("zone_a_via_x").value)
        self.zone_a_via_y = float(self.get_parameter("zone_a_via_y").value)

        self.zone_b_min_x = float(self.get_parameter("zone_b_min_x").value)
        self.zone_b_max_x = float(self.get_parameter("zone_b_max_x").value)
        self.zone_b_via_x = float(self.get_parameter("zone_b_via_x").value)
        self.zone_b_via_y = float(self.get_parameter("zone_b_via_y").value)

        self.zone_c_min_x = float(self.get_parameter("zone_c_min_x").value)
        self.zone_c_max_x = float(self.get_parameter("zone_c_max_x").value)
        self.zone_c_via_x = float(self.get_parameter("zone_c_via_x").value)
        self.zone_c_via_y = float(self.get_parameter("zone_c_via_y").value)
        # ===================================

        self.goal_x = float(self.get_parameter("goal_x").value)
        self.goal_y = float(self.get_parameter("goal_y").value)

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

        self.auto_exit_on_complete = bool(self.get_parameter("auto_exit_on_complete").value)
        self.exit_delay_sec = float(self.get_parameter("exit_delay_sec").value)

        self.final_stop_total_sec = float(self.get_parameter("final_stop_total_sec").value)
        self.final_stop_count = int(self.get_parameter("final_stop_count").value)

        self.soft_reset_stop_repeat = int(self.get_parameter("soft_reset_stop_repeat").value)
        self.soft_reset_stop_dt = float(self.get_parameter("soft_reset_stop_dt").value)
        self.enable_costmap_clear = bool(self.get_parameter("enable_costmap_clear").value)
        self.costmap_clear_services = list(self.get_parameter("costmap_clear_services").value)

        # PID
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
        self.nav2_goal_handle = None
        self._nav2_goal_sent = False
        self._settle_until = None
        self._exit_timer = None

        # ====== (ADD) start-once policy flag ======
        self._start_policy_decided = False
        # =========================================

        # Nav2
        self.nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")

        # ROS I/O
        self.pose_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose",
            self.amcl_callback, 10
        )

        # Nav2 단계에서는 /cmd_vel 건드리지 않지만, PID 단계에서 사용
        self.cmd_vel_docking_pub = self.create_publisher(Twist, "/cmd_vel_docking", 10)
        self.docking_status_pub = self.create_publisher(String, "/docking_status", 10)

        # timers
        self.control_timer = self.create_timer(0.05, self.control_loop)
        self.retry_timer = self.create_timer(self.nav2_retry_period_sec, self._try_send_goal_if_needed)

        # ✅ 시작 stage 결정 (pre-waypoint가 켜져있으면 PRE부터)
        if self.enable_pre_waypoint:
            self.stage = "PRE_WP_NAV2"
        else:
            if self.enable_waypoint:
                self.stage = "WP_NAV2"
            else:
                self.stage = "FINAL_NAV2"

        self.get_logger().info(
            f"[START] stage={self.stage} "
            f"pre_wp=({self.pre_waypoint_x:.3f},{self.pre_waypoint_y:.3f}) pre_tol={self.pre_waypoint_reach_tolerance:.2f} pre_en={self.enable_pre_waypoint} "
            f"wp=({self.waypoint_x:.3f},{self.waypoint_y:.3f}) wp_tol={self.waypoint_reach_tolerance:.2f} wp_en={self.enable_waypoint} "
            f"goal=({self.goal_x:.3f},{self.goal_y:.3f}) switch={self.switch_distance:.2f} "
            f"soft_reset(costmap_clear={self.enable_costmap_clear})"
        )

    # ====== (ADD) match zone via by CURRENT_X (start-once) ======
    def _match_zone_via_by_current_x(self, current_x: float):
        if self.zone_a_min_x <= current_x <= self.zone_a_max_x:
            return "A", (self.zone_a_via_x, self.zone_a_via_y)
        if self.zone_b_min_x <= current_x <= self.zone_b_max_x:
            return "B", (self.zone_b_via_x, self.zone_b_via_y)
        if self.zone_c_min_x <= current_x <= self.zone_c_max_x:
            return "C", (self.zone_c_via_x, self.zone_c_via_y)
        return None, None
    # ============================================================

    def _now_sec(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def amcl_callback(self, msg: PoseWithCovarianceStamped):
        pose = msg.pose.pose
        self.current_pose = (
            float(pose.position.x),
            float(pose.position.y),
            float(yaw_from_quaternion(pose.orientation))
        )

        # ====== (ADD) START-ONCE: decide PRE waypoint by current_x zone ======
        if not self._start_policy_decided:
            self._start_policy_decided = True

            current_x = float(self.current_pose[0])
            zone, via = self._match_zone_via_by_current_x(current_x)

            if via is not None:
                via_x, via_y = via

                # zone 매칭되면: PRE waypoint를 via로 덮어쓰기
                self.pre_waypoint_x = float(via_x)
                self.pre_waypoint_y = float(via_y)

                # PRE 단계부터 시작하도록 강제
                self.enable_pre_waypoint = True
                self.stage = "PRE_WP_NAV2"
                self._nav2_goal_sent = False

                self.get_logger().info(
                    f"[START_POLICY] current_x={current_x:.3f} -> ZONE_{zone} via=({via_x:.3f},{via_y:.3f}) : start PRE_WP_NAV2"
                )
            else:
                # zone 아니면: PRE는 스킵하고 기존 정책대로 (WP 있으면 WP, 없으면 FINAL)
                self.enable_pre_waypoint = False

                if getattr(self, "enable_waypoint", False):
                    self.stage = "WP_NAV2"
                    self.get_logger().info(
                        f"[START_POLICY] current_x={current_x:.3f} -> no zone: skip PRE, start WP_NAV2"
                    )
                else:
                    self.stage = "FINAL_NAV2"
                    self.get_logger().info(
                        f"[START_POLICY] current_x={current_x:.3f} -> no zone: skip PRE, start FINAL_NAV2"
                    )

                self._nav2_goal_sent = False
        # ====================================================================

    def publish_docking_status(self, text: str):
        msg = String()
        msg.data = str(text)
        self.docking_status_pub.publish(msg)

    # ==========================
    # Nav2 goal send/cancel
    # ==========================
    def _send_nav2_goal(self, x, y, yaw=0.0) -> bool:
        if not self.nav_client.wait_for_server(timeout_sec=self.nav2_wait_timeout_sec):
            return False

        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(x)
        goal.pose.pose.position.y = float(y)
        goal.pose.pose.orientation.z = math.sin(float(yaw) / 2.0)
        goal.pose.pose.orientation.w = math.cos(float(yaw) / 2.0)

        future = self.nav_client.send_goal_async(goal)
        future.add_done_callback(self._nav2_goal_response_cb)

        self._nav2_goal_sent = True
        self.get_logger().info(f"[NAV2] sent: ({x:.3f},{y:.3f})")
        return True

    def _nav2_goal_response_cb(self, future):
        try:
            goal_handle = future.result()
        except Exception as e:
            self.get_logger().warn(f"[NAV2] goal response error: {e}")
            self.nav2_goal_handle = None
            self._nav2_goal_sent = False
            return

        if not goal_handle.accepted:
            self.get_logger().warn("[NAV2] goal rejected")
            self.nav2_goal_handle = None
            self._nav2_goal_sent = False
            return

        self.get_logger().info("[NAV2] goal accepted")
        self.nav2_goal_handle = goal_handle
        goal_handle.get_result_async()

    def _cancel_nav2_goal_blocking(self, timeout_sec: float = 1.0) -> bool:
        if self.nav2_goal_handle is None:
            return True
        try:
            fut = self.nav2_goal_handle.cancel_goal_async()
            rclpy.spin_until_future_complete(self, fut, timeout_sec=timeout_sec)
            self.get_logger().info("[NAV2] cancel requested (blocking wait done)")
        except Exception as e:
            self.get_logger().warn(f"[NAV2] cancel failed: {e}")
            return False
        finally:
            self.nav2_goal_handle = None
        return True

    # ==========================
    # Goal sending policy
    # ==========================
    def _try_send_goal_if_needed(self):
        if self.stage in ("FINISH", "PID_POS", "PID_FINE", "PID_YAW"):
            return
        if self._settle_until is not None:
            return
        if self._nav2_goal_sent:
            return

        if self.stage == "PRE_WP_NAV2":
            self._send_nav2_goal(self.pre_waypoint_x, self.pre_waypoint_y, NAV2_GOAL_YAW)
        elif self.stage == "WP_NAV2":
            self._send_nav2_goal(self.waypoint_x, self.waypoint_y, NAV2_GOAL_YAW)
        elif self.stage == "FINAL_NAV2":
            self._send_nav2_goal(self.goal_x, self.goal_y, NAV2_GOAL_YAW)

    # ==========================
    # PID publish (PID에서만 사용)
    # ==========================
    def _publish_cmd(self, v, w):
        msg = Twist()
        msg.linear.x = float(v)
        msg.angular.z = float(w)
        self.cmd_vel_docking_pub.publish(msg)

    def _publish_stop_once(self):
        msg = Twist()
        msg.linear.x = 0.0
        msg.angular.z = 0.0
        self.cmd_vel_docking_pub.publish(msg)

    # ==========================
    # final_yaw_aligned 후 3초 동안 10번 정지 명령
    # ==========================
    def _final_stop_3sec_10times(self):
        total = max(0.0, float(self.final_stop_total_sec))
        count = max(1, int(self.final_stop_count))
        dt = total / float(count) if count > 0 else 0.0

        for _ in range(count):
            self._publish_stop_once()
            rclpy.spin_once(self, timeout_sec=dt if dt > 0.0 else 0.01)

    # ==========================
    # ✅ costmap clear entirely
    # ==========================
    def _clear_costmaps_entirely(self):
        if not self.costmap_clear_services:
            return

        for svc_name in self.costmap_clear_services:
            try:
                client = self.create_client(ClearEntireCostmap, svc_name)
                if not client.wait_for_service(timeout_sec=0.5):
                    self.get_logger().warn(f"[SOFT_RESET] costmap svc not ready: {svc_name}")
                    self.destroy_client(client)
                    continue

                req = ClearEntireCostmap.Request()
                fut = client.call_async(req)
                rclpy.spin_until_future_complete(self, fut, timeout_sec=1.0)

                self.get_logger().info(f"[SOFT_RESET] costmap cleared: {svc_name}")
                self.destroy_client(client)
            except Exception as e:
                self.get_logger().warn(f"[SOFT_RESET] costmap clear failed {svc_name}: {e}")

    # ==========================
    # ✅ 소프트 리셋(브링업 다시 킨 듯)
    # ==========================
    def _soft_reset_like_rebringup(self, reason: str = ""):
        self.get_logger().warn(f"[SOFT_RESET] start. reason={reason}")

        # 1) 남아있는 Nav2 goal 있으면 끊기
        self._cancel_nav2_goal_blocking(timeout_sec=1.0)

        # 2) stop 여러 번 (PID가 돌며 빙글빙글 방지)
        repeat = max(1, int(self.soft_reset_stop_repeat))
        dt = max(0.0, float(self.soft_reset_stop_dt))
        for _ in range(repeat):
            self._publish_stop_once()
            rclpy.spin_once(self, timeout_sec=dt if dt > 0.0 else 0.01)

        # 3) 내부 상태 reset
        self._nav2_goal_sent = False
        self.linear_pid.reset()
        self.angular_pid.reset()

        # 4) costmap clear entirely (Nav2는 살려둠)
        if self.enable_costmap_clear:
            self._clear_costmaps_entirely()

        self._publish_stop_once()
        self.get_logger().warn("[SOFT_RESET] done.")

    # ==========================
    # finish
    # ==========================
    def _finish(self, reason: str):
        if self.stage == "FINISH":
            return
        self.stage = "FINISH"

        # 마지막 안전: nav2 goal 끊기 + 내부/코스트맵 정리
        self._soft_reset_like_rebringup(reason=reason)

        self.publish_docking_status("주행완료")
        self.get_logger().info(f"주행완료 ({reason})")

        if self.auto_exit_on_complete:
            self._schedule_shutdown()

    def _schedule_shutdown(self):
        if self._exit_timer is not None:
            return

        delay = max(0.0, float(self.exit_delay_sec))

        def _do_shutdown():
            # 종료 직전에 stop 한 번 더
            try:
                self._publish_stop_once()
            except Exception:
                pass

            rclpy.shutdown()
            try:
                sys.exit(0)
            except SystemExit:
                pass

        self._exit_timer = self.create_timer(delay, _do_shutdown)

    # ==========================
    # main loop
    # ==========================
    def control_loop(self):
        if self.stage == "FINISH":
            return

        # settle 대기
        if self._settle_until is not None:
            if self._now_sec() < self._settle_until:
                return
            self._settle_until = None
            self._nav2_goal_sent = False

            # ✅ settle 후 다음 단계 결정
            if self.stage == "PRE_WP_SETTLE":
                if self.enable_waypoint:
                    self.stage = "WP_NAV2"
                    self.get_logger().info("[PRE_WP] settle done -> send WP")
                else:
                    self.stage = "FINAL_NAV2"
                    self.get_logger().info("[PRE_WP] settle done -> send FINAL (waypoint disabled)")
                return

            if self.stage == "WP_SETTLE":
                self.stage = "FINAL_NAV2"
                self.get_logger().info("[WAYPOINT] settle done -> send FINAL")
                return

            return

        if self.current_pose is None:
            return

        x, y, yaw = self.current_pose
        now_sec = self._now_sec()

        # 0) PRE_WAYPOINT: Nav2만
        if self.stage == "PRE_WP_NAV2":
            dist_pre = sqrt((self.pre_waypoint_x - x) ** 2 + (self.pre_waypoint_y - y) ** 2)

            if dist_pre < self.pre_waypoint_reach_tolerance:
                self.get_logger().info(
                    f"[PRE_WP] reached tol: {dist_pre:.3f} < {self.pre_waypoint_reach_tolerance:.3f} "
                    f"-> CANCEL NAV2 (blocking) -> settle {self.pre_waypoint_settle_sec:.2f}s -> NEXT"
                )
                self._cancel_nav2_goal_blocking(timeout_sec=1.0)

                self._nav2_goal_sent = False
                self.stage = "PRE_WP_SETTLE"
                self._settle_until = now_sec + max(0.0, float(self.pre_waypoint_settle_sec))
            return

        # 1) WAYPOINT: Nav2만
        if self.stage == "WP_NAV2":
            dist_wp = sqrt((self.waypoint_x - x) ** 2 + (self.waypoint_y - y) ** 2)

            if dist_wp < self.waypoint_reach_tolerance:
                self.get_logger().info(
                    f"[WAYPOINT] reached tol: {dist_wp:.3f} < {self.waypoint_reach_tolerance:.3f} "
                    f"-> CANCEL NAV2 (blocking) -> settle {self.waypoint_settle_sec:.2f}s -> FINAL"
                )
                self._cancel_nav2_goal_blocking(timeout_sec=1.0)

                self._nav2_goal_sent = False
                self.stage = "WP_SETTLE"
                self._settle_until = now_sec + max(0.0, float(self.waypoint_settle_sec))
            return

        # 2) FINAL: Nav2 -> PID
        if self.stage == "FINAL_NAV2":
            dist_goal = sqrt((self.goal_x - x) ** 2 + (self.goal_y - y) ** 2)

            if dist_goal < self.switch_distance:
                self.get_logger().info(
                    f"[FINAL] near switch: {dist_goal:.3f} < {self.switch_distance:.3f} "
                    f"-> CANCEL NAV2 (blocking) -> PID"
                )
                self._cancel_nav2_goal_blocking(timeout_sec=1.0)

                self.linear_pid.reset()
                self.angular_pid.reset()
                self.stage = "PID_POS"
            return

        # 3) PID_POS / PID_FINE / PID_YAW
        if self.stage == "PID_POS":
            dx = self.goal_x - x
            dy = self.goal_y - y
            distance = sqrt(dx * dx + dy * dy)

            desired_heading = atan2(dy, dx)
            heading_error = normalize_angle(desired_heading - yaw)

            v_cmd = self.linear_pid.update(distance, now_sec)
            w_cmd = self.angular_pid.update(heading_error, now_sec)

            if abs(heading_error) > 0.7:
                v_cmd = 0.0

            self._publish_cmd(v_cmd, w_cmd)

            if distance < self.pos_distance_tolerance:
                self.linear_pid.reset()
                self.angular_pid.reset()
                self.stage = "PID_FINE"
            return

        if self.stage == "PID_FINE":
            dx = self.goal_x - x
            dy = self.goal_y - y
            distance = sqrt(dx * dx + dy * dy)

            desired_heading = atan2(dy, dx)
            heading_error = normalize_angle(desired_heading - yaw)

            if distance < self.fine_distance_tolerance:
                self.angular_pid.reset()
                self.stage = "PID_YAW"
                return

            w_cmd = self.angular_pid.update(heading_error, now_sec)
            if abs(heading_error) > 0.5:
                v_cmd = 0.0
            else:
                v_cmd = self.linear_pid.update(distance, now_sec)

            self._publish_cmd(v_cmd * 0.5, w_cmd * 0.5)
            return

        if self.stage == "PID_YAW":
            yaw_error = normalize_angle(FINAL_YAW_CAMERA_DOWN - yaw)

            if abs(yaw_error) < self.final_yaw_tolerance:
                self.publish_docking_status("DOCKING_COMPLETE")

                # ✅ 요구사항 유지: final_yaw_aligned 완료되면 cmd_vel=0을 3초 동안 10번
                self._final_stop_3sec_10times()

                # ✅ kill/restart 없이 소프트 리셋 + 주행완료 + 종료
                self._finish(reason="final_yaw_aligned")
                return

            w_cmd = self.angular_pid.update(yaw_error, now_sec)
            self._publish_cmd(0.0, w_cmd)
            return


def main(args=None):
    rclpy.init(args=args)
    node = PinkyChargeClean()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.destroy_node()
        except Exception:
            pass
        try:
            if rclpy.ok():
                rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()