from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, ExecuteProcess
from launch.launch_description_sources import AnyLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import PathJoinSubstitution


def generate_launch_description():

    map_yaml = LaunchConfiguration("map")

    # 1️⃣ Pinky 브링업
    pinky_bringup = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(
            PathJoinSubstitution([
                FindPackageShare("pinky_bringup"),
                "launch",
                "bringup_robot.launch.xml"
            ])
        )
    )

    # 2️⃣ Nav2 + 맵
    nav2_bringup = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(
            PathJoinSubstitution([
                FindPackageShare("pinky_navigation"),
                "launch",
                "bringup_launch.xml"
            ])
        ),
        launch_arguments={
            "map": map_yaml
        }.items(),
    )

    # 3️⃣ 네가 준 YOLO cmd_vel arbiter
    cmd_vel_node = ExecuteProcess(
        cmd=["python3", "/home/pinky/pinky_pro/src/my_pinky_launch/scripts/cmd_vel_arbiter_yolo.py"],
        output="screen",
    )

    # 4️⃣ 네가 준 LED 노드
    led_node = ExecuteProcess(
        cmd=["python3", "/home/pinky/pinky_pro/src/my_pinky_launch/scripts/led.py"],
        output="screen",
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            "map",
            default_value="pix_map.yaml"
        ),
        pinky_bringup,
        nav2_bringup,
        cmd_vel_node,
        led_node
    ])
