"""Two-robot orbit. The same file runs on the robots and in the simulator.

Robot 0 holds still, lights blue, and broadcasts its pose.
Robot 1 orbits that pose at about 0.5 m. Its LED follows the orbit angle.
Wheel commands are sent on every loop because a hardware set_vel lasts one
second, and both wheels stay within +/- 50.

Python 2.7 and Python 3 can both parse this file. The robots run Python
2.7.16. The simulator imports it under Python 3. virtual_id is a method on
the robots and a number in the simulator.
"""

import math
import struct

ORBIT_RADIUS = 0.5
BASE_SPEED = 20.0
K_RADIAL = 4.0
K_TURN = 12.0
MAX_WHEEL = 50.0


def clamp(value, low, high):
    """Return value limited to the inclusive range [low, high]."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def wrap_to_pi(angle):
    """Wrap an angle in radians into (-pi, pi]."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def robot_id(robot):
    """Return the id from init_pose.csv on hardware and in simulation."""
    virtual_id = getattr(robot, "virtual_id", None)
    if callable(virtual_id):
        return int(virtual_id())
    if virtual_id is not None:
        return int(virtual_id)
    return int(robot.id)


def led_from_angle(angle):
    """Map an orbit angle in radians to LED channels in 0..100."""
    phase = (angle + math.pi) / (2.0 * math.pi)
    red = int(100.0 * (1.0 - phase))
    green = int(100.0 * phase)
    blue = int(50.0 + 50.0 * math.sin(angle))
    return int(clamp(red, 0, 100)), int(clamp(green, 0, 100)), int(clamp(blue, 0, 100))


def usr(robot):
    """Entry point the robot runtime and the simulator both call."""
    rid = robot_id(robot)
    center = None
    robot.delay(1000)
    while True:
        pose = robot.get_pose()
        if not pose:
            robot.set_vel(0, 0)
            robot.delay(20)
            continue
        x = float(pose[0])
        y = float(pose[1])
        theta = float(pose[2])

        if rid == 0:
            robot.set_led(0, 0, 100)
            robot.set_vel(0, 0)
            robot.send_msg(struct.pack("ff", x, y))
            robot.delay(50)
            continue

        msgs = robot.recv_msg()
        if msgs:
            for raw in msgs:
                if isinstance(raw, bytes) and len(raw) >= 8:
                    cx, cy = struct.unpack("ff", raw[:8])
                    center = (cx, cy)
        if center is None:
            robot.set_led(100, 0, 0)
            robot.set_vel(0, 0)
            robot.delay(50)
            continue

        cx, cy = center
        dx = x - cx
        dy = y - cy
        radius = math.hypot(dx, dy)
        if radius < 1e-4:
            radius = 1e-4
        orbit_angle = math.atan2(dy, dx)
        red, green, blue = led_from_angle(orbit_angle)
        robot.set_led(red, green, blue)
        outward_x = dx / radius
        outward_y = dy / radius
        tangent_x = -outward_y
        tangent_y = outward_x
        radial_error = radius - ORBIT_RADIUS
        desired_x = tangent_x - outward_x * (K_RADIAL * radial_error)
        desired_y = tangent_y - outward_y * (K_RADIAL * radial_error)
        heading_error = wrap_to_pi(math.atan2(desired_y, desired_x) - theta)
        turn = clamp(K_TURN * heading_error, -MAX_WHEEL, MAX_WHEEL)
        speed = BASE_SPEED
        if abs(heading_error) > 1.0:
            speed = BASE_SPEED * 0.4
        robot.set_vel(
            clamp(speed - turn, -MAX_WHEEL, MAX_WHEEL),
            clamp(speed + turn, -MAX_WHEEL, MAX_WHEEL),
        )
        robot.delay(50)
