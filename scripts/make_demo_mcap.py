"""Write a demo MCAP recording that looks like a small robot recording (for the README screenshot and for trying out).

Topics use real ROS 2 message types with their definitions and the QoS metadata rosbag2 writes, so the GUI shows
names, schemas, rates, sizes and latched topics as for a real recording. The message contents are filler of a
realistic size (not decodable CDR). The recording has typical problems to look at: a GNSS outage from 35 to 47 s and
a camera that stops at 70 s. Randomness uses a fixed seed, so every run writes the same file.

Usage:
    uv run python scripts/make_demo_mcap.py demo.mcap
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from mcap.writer import Writer

T0 = 1_778_752_800 * 10**9          # 2026-05-14 10:00:00 UTC
S = 10**9
DURATION_S = 90

QOS_VOLATILE = ("- history: keep_last\n  depth: 10\n  reliability: reliable\n  durability: volatile\n"
                "  deadline:\n    sec: 9223372036\n    nsec: 854775807\n  lifespan:\n    sec: 9223372036\n"
                "    nsec: 854775807\n  liveliness: automatic\n  liveliness_lease_duration:\n    sec: 9223372036\n"
                "    nsec: 854775807\n  avoid_ros_namespace_conventions: false\n")
QOS_LATCHED_1 = QOS_VOLATILE.replace("depth: 10", "depth: 1").replace("volatile", "transient_local")
QOS_LATCHED_1000 = QOS_VOLATILE.replace("depth: 10", "depth: 1000").replace("volatile", "transient_local")

HEADER = "builtin_interfaces/Time stamp\nstring frame_id\n"
TIME = "int32 sec\nuint32 nanosec\n"
SEP = "=" * 80 + "\n"


def deps(*parts: tuple[str, str]) -> str:
    """Dependency sections of a ros2msg schema."""
    return "".join(f"{SEP}MSG: {name}\n{text}" for name, text in parts)


SCHEMAS = {
    "sensor_msgs/msg/Imu": (
        "std_msgs/Header header\ngeometry_msgs/Quaternion orientation\nfloat64[9] orientation_covariance\n"
        "geometry_msgs/Vector3 angular_velocity\nfloat64[9] angular_velocity_covariance\n"
        "geometry_msgs/Vector3 linear_acceleration\nfloat64[9] linear_acceleration_covariance\n"
        + deps(("std_msgs/Header", HEADER), ("builtin_interfaces/Time", TIME),
               ("geometry_msgs/Quaternion", "float64 x 0\nfloat64 y 0\nfloat64 z 0\nfloat64 w 1\n"),
               ("geometry_msgs/Vector3", "float64 x\nfloat64 y\nfloat64 z\n"))),
    "sensor_msgs/msg/NavSatFix": (
        "std_msgs/Header header\nsensor_msgs/NavSatStatus status\nfloat64 latitude\nfloat64 longitude\n"
        "float64 altitude\nfloat64[9] position_covariance\nuint8 position_covariance_type\n"
        + deps(("std_msgs/Header", HEADER), ("builtin_interfaces/Time", TIME),
               ("sensor_msgs/NavSatStatus", "int8 status\nuint16 service\n"))),
    "sensor_msgs/msg/CompressedImage": (
        "std_msgs/Header header\nstring format\nuint8[] data\n"
        + deps(("std_msgs/Header", HEADER), ("builtin_interfaces/Time", TIME))),
    "nav_msgs/msg/Odometry": (
        "std_msgs/Header header\nstring child_frame_id\ngeometry_msgs/PoseWithCovariance pose\n"
        "geometry_msgs/TwistWithCovariance twist\n"
        + deps(("std_msgs/Header", HEADER), ("builtin_interfaces/Time", TIME),
               ("geometry_msgs/PoseWithCovariance", "geometry_msgs/Pose pose\nfloat64[36] covariance\n"),
               ("geometry_msgs/TwistWithCovariance", "geometry_msgs/Twist twist\nfloat64[36] covariance\n"))),
    "tf2_msgs/msg/TFMessage": (
        "geometry_msgs/TransformStamped[] transforms\n"
        + deps(("geometry_msgs/TransformStamped", "std_msgs/Header header\nstring child_frame_id\n"
                                                  "geometry_msgs/Transform transform\n"),
               ("std_msgs/Header", HEADER), ("builtin_interfaces/Time", TIME))),
    "rcl_interfaces/msg/Log": (
        "byte DEBUG=10\nbyte INFO=20\nbyte WARN=30\nbyte ERROR=40\nbyte FATAL=50\n"
        "builtin_interfaces/Time stamp\nuint8 level\nstring name\nstring msg\nstring file\nstring function\n"
        "uint32 line\n" + deps(("builtin_interfaces/Time", TIME))),
    "diagnostic_msgs/msg/DiagnosticArray": (
        "std_msgs/Header header\ndiagnostic_msgs/DiagnosticStatus[] status\n"
        + deps(("std_msgs/Header", HEADER), ("builtin_interfaces/Time", TIME),
               ("diagnostic_msgs/DiagnosticStatus", "byte level\nstring name\nstring message\nstring hardware_id\n"
                                                    "diagnostic_msgs/KeyValue[] values\n"),
               ("diagnostic_msgs/KeyValue", "string key\nstring value\n"))),
}

# topic: (schema, QoS, rate in Hz or None, message size in bytes, (start s, end s) or gaps as [(from, to)])
TOPICS = {
    "/imu/data": ("sensor_msgs/msg/Imu", QOS_VOLATILE, 200, 324),
    "/gnss/fix": ("sensor_msgs/msg/NavSatFix", QOS_VOLATILE, 10, 128),
    "/camera/front/image_raw/compressed": ("sensor_msgs/msg/CompressedImage", QOS_VOLATILE, 15, 12_000),
    "/odom": ("nav_msgs/msg/Odometry", QOS_VOLATILE, 50, 720),
    "/tf": ("tf2_msgs/msg/TFMessage", QOS_VOLATILE, 50, 180),
    "/tf_static": ("tf2_msgs/msg/TFMessage", QOS_LATCHED_1, None, 860),
    "/rosout": ("rcl_interfaces/msg/Log", QOS_LATCHED_1000, None, 160),
    "/diagnostics": ("diagnostic_msgs/msg/DiagnosticArray", QOS_VOLATILE, 1, 2_400),
}


def times(topic: str, rng: random.Random) -> list[int]:
    """Log times (ns) of the messages of ``topic``, with the demo's gaps and stops."""
    rate = TOPICS[topic][2]
    if topic == "/tf_static":
        return [T0]
    if topic == "/rosout":                     # a few log lines, a burst when the GNSS drops out
        ts = sorted(rng.uniform(0, DURATION_S) for _ in range(40)) + [35 + i * 0.2 for i in range(12)]
        return sorted(T0 + int(t * S) for t in ts)
    out = []
    for i in range(int(DURATION_S * rate)):
        t = i / rate
        if topic == "/gnss/fix" and 35 <= t < 47:          # GNSS outage
            continue
        if topic.startswith("/camera") and t >= 70:        # camera stops
            break
        jitter = rng.uniform(-0.1, 0.1) / rate if topic.startswith("/camera") else 0
        out.append(T0 + int((t + jitter) * S) + 1)
    return out


def write_demo(path: Path) -> None:
    """Write the demo recording to ``path``."""
    rng = random.Random(42)
    with open(path, "wb") as f:
        w = Writer(f)
        w.start(profile="ros2", library="mcap_editor_ui demo")
        sids = {name: w.register_schema(name, "ros2msg", text.encode()) for name, text in SCHEMAS.items()}
        chans = {t: w.register_channel(t, "cdr", sids[schema], {"offered_qos_profiles": qos})
                 for t, (schema, qos, _, _) in TOPICS.items()}
        events = sorted((t_ns, topic) for topic in TOPICS for t_ns in times(topic, rng))
        filler = rng.randbytes(16_000)
        for seq, (t_ns, topic) in enumerate(events):
            size = TOPICS[topic][3]
            if topic.startswith("/camera"):                  # images compress badly: random content
                data = rng.randbytes(size + rng.randint(-1500, 1500))
            else:
                data = filler[(seq * 97) % 4000:][:size]
            w.add_message(chans[topic], log_time=t_ns, data=data, publish_time=t_ns, sequence=seq)
        w.add_metadata("demo", {"note": "synthetic recording for MCAP Editor UI"})
        w.finish()


def main() -> None:
    """Command line: output path."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("out", type=Path, help="output MCAP file (overwritten)")
    a = p.parse_args()
    write_demo(a.out)
    print(f"written: {a.out} ({a.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
