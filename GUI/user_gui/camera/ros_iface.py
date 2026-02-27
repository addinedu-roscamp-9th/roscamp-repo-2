import math
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from std_msgs.msg import String, Int32


def _yaw_from_quaternion(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


class RosInterface(Node):
    """
    - /click_goal publish (PoseStamped: position + orientation)
    - /docking_status subscribe
    - /battery_percent subscribe (Int32, 0~100)
    - /amcl_pose subscribe -> current_pose 저장
    """

    def __init__(self):
        super().__init__("click_to_pid_goal")

        self.pub = self.create_publisher(PoseStamped, "click_goal", 10)

        self.last_docking_status = None
        self.sub_docking = self.create_subscription(
            String, "/docking_status", self._docking_status_cb, 10
        )

        self.battery_percent = None
        self.sub_battery = self.create_subscription(
            Int32, "/battery_percent", self._battery_cb, 10
        )

        self.current_pose = None  # (x,y,yaw) or None
        self.sub_amcl = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self._amcl_cb, 10
        )

    def publish_goal(self, x: float, y: float, yaw: float = 0.0, is_final: bool = False):
        """
        ✅ is_final=True 인 goal만 yaw 정렬(최종 방향)을 하게 만들기 위한 플래그 포함.
        PoseStamped.position.z 를 플래그로 사용:
          - z=1.0 : FINAL goal
          - z=0.0 : waypoint
        """
        msg = PoseStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()

        msg.pose.position.x = float(x)
        msg.pose.position.y = float(y)
        msg.pose.position.z = 1.0 if bool(is_final) else 0.0  # ✅ FINAL 플래그

        msg.pose.orientation.z = math.sin(float(yaw) / 2.0)
        msg.pose.orientation.w = math.cos(float(yaw) / 2.0)

        self.pub.publish(msg)

    def distance_to(self, x: float, y: float):
        if self.current_pose is None:
            return None
        rx, ry, _ = self.current_pose
        dx = float(x) - rx
        dy = float(y) - ry
        return math.sqrt(dx * dx + dy * dy)

    def _amcl_cb(self, msg: PoseWithCovarianceStamped):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        self.current_pose = (float(p.x), float(p.y), float(_yaw_from_quaternion(q)))

    def _docking_status_cb(self, msg: String):
        self.last_docking_status = msg.data

    def _battery_cb(self, msg: Int32):
        v = int(msg.data)
        if v < 0:
            v = 0
        if v > 100:
            v = 100
        self.battery_percent = v
