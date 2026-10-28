"""Run the pinned swarm simulator headlessly for a short smoke test.

The simulator is michelleezhang/swarm-simulator. User code is copied into a
temporary sim_pkg/user directory and imported by the simulator under Python 3.
USE_VIS is 0, so pygame never opens a window. SDL is still pointed at a dummy
driver in case a future simulator build initializes it on import.

The child process does not inherit GitHub tokens. User code runs inside that
child, on the GitHub-hosted runner, and never on the lab computer here.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

SIMULATOR_REPO = "https://github.com/michelleezhang/swarm-simulator.git"
SIMULATOR_COMMIT = "25c8ea31589cdc4c7371be7b371c4774853f0483"
REAL_TIME_FACTOR = 100
COMM_RANGE_M = 5.0
MSG_SIZE = 64
NUM_OF_MSGS = 8

_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "USER", "TMPDIR", "TEMP", "TMP")


def simulation_config(num_robots: int, sim_seconds: float) -> dict[str, object]:
    """Build the simulator config for one smoke test.

    REAL_TIME_FACTOR 100 is the simulator's documented maximum. MSG_SIZE 64
    matches the hardware message cap and stays under the simulator's 1024-byte
    socket read. MSG_TYPE 1 keeps struct.pack bytes intact. COMM_RANGE is wide
    because the physical swarm is all-to-all; a short radio would fail code
    that is legal on the robots.
    """
    return {
        "NUMBER_OF_ROBOTS": num_robots,
        "COMM_RANGE": COMM_RANGE_M,
        "PACKET_SUCCESS_PERC": 1.0,
        "NUM_OF_MSGS": NUM_OF_MSGS,
        "MSG_SIZE": MSG_SIZE,
        "MSG_TYPE": 1,
        "TIME_ASYNC": 0,
        "REAL_TIME_FACTOR": REAL_TIME_FACTOR,
        "SIM_TIME_STEP": 0.05,
        "SIM_TIME": sim_seconds,
        "WIDTH": 10,
        "LENGTH": 10,
        "USE_VIS": 0,
        "COLLISION_CHECK": 1,
    }


def simulator_env() -> dict[str, str]:
    """Environment for the simulator process, without Actions credentials."""
    env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
    env["MPLBACKEND"] = "Agg"
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    env["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def ensure_simulator(dest: Path, repo: str = SIMULATOR_REPO, commit: str = SIMULATOR_COMMIT) -> Path:
    """Clone the pinned simulator into dest if that commit is not already there.

    An existing checkout of a different commit is left untouched and reported
    as an error, so a local experiment is not overwritten.
    """
    marker = dest / "sim_pkg" / "coachbot_simulator.py"
    git_dir = dest / ".git"
    if marker.is_file() and git_dir.exists():
        completed = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        head = completed.stdout.strip()
        if completed.returncode == 0 and head == commit:
            return dest
        raise RuntimeError(
            "simulator at %s is commit %s, expected %s" % (dest, head or "unknown", commit)
        )
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError("refusing to clone into non-empty directory %s" % dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "remote", "add", "origin", repo],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    )
    fetch = subprocess.run(
        ["git", "fetch", "--depth", "1", "origin", commit],
        cwd=dest,
        check=False,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        detail = (fetch.stderr or fetch.stdout or "git fetch failed").strip()
        raise RuntimeError("could not fetch the simulator: %s" % detail)
    subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=dest, check=True, capture_output=True, text=True)
    if not marker.is_file():
        raise RuntimeError("simulator checkout is missing sim_pkg/coachbot_simulator.py")
    return dest


def _wall_timeout(sim_seconds: float) -> int:
    """Seconds to wait before killing a hung simulator.

    Configured real-time factor 100 turns 60 simulated seconds into under a
    second of integration, plus the simulator's 2 second startup sleep. The
    pad covers a slow runner without letting a stuck socket sit for the whole
    job timeout.
    """
    scaled = sim_seconds / float(REAL_TIME_FACTOR)
    return int(max(90, scaled * 5 + 30))


def _last_sim_time(log_path: Path) -> float | None:
    if not log_path.is_file():
        return None
    last = None
    with log_path.open(newline="") as handle:
        for row in csv.reader(handle):
            if not row:
                continue
            try:
                last = float(row[0])
            except ValueError:
                continue
    return last


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text.strip()
    return text[-limit:].strip()


def run_headless_simulation(
    usr_code: Path,
    init_pose_text: str,
    sim_seconds: float,
    simulator_dir: Path | None,
    log_dir: Path | None,
) -> dict[str, object]:
    """Execute one headless simulation and return a validation check dict."""
    robot_count = sum(1 for line in init_pose_text.splitlines() if line.strip())
    if robot_count < 1:
        return {"name": "simulator", "ok": False, "detail": "no robots to simulate"}
    try:
        source = ensure_simulator(simulator_dir or Path("third_party/swarm-simulator"))
    except (RuntimeError, subprocess.CalledProcessError) as err:
        return {"name": "simulator", "ok": False, "detail": str(err)}

    work = Path(tempfile.mkdtemp(prefix="coachbot-sim-"))
    try:
        sim_pkg = work / "sim_pkg"
        shutil.copytree(source / "sim_pkg", sim_pkg)
        user_dir = sim_pkg / "user"
        user_dir.mkdir(exist_ok=True)
        init_file = user_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("", encoding="utf-8")
        (user_dir / "usr_code.py").write_bytes(usr_code.read_bytes())
        (user_dir / "init_pose.csv").write_text(init_pose_text, encoding="utf-8")
        config = simulation_config(robot_count, sim_seconds)
        (user_dir / "ci_config.json").write_text(json.dumps(config), encoding="utf-8")

        proc = subprocess.Popen(
            [
                sys.executable,
                "coachbot_simulator.py",
                "-u",
                "usr_code.py",
                "-c",
                "ci_config.json",
                "-i",
                "init_pose.csv",
            ],
            cwd=sim_pkg,
            env=simulator_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=_wall_timeout(sim_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        stdout = stdout or ""
        stderr = stderr or ""
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
            (log_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
            state_log = sim_pkg / "swarm_state_log.csv"
            if state_log.is_file():
                shutil.copy2(state_log, log_dir / "swarm_state_log.csv")
        combined = stdout + "\n" + stderr
        if timed_out:
            return {
                "name": "simulator",
                "ok": False,
                "detail": "simulator exceeded the wall-clock limit\n" + _tail(combined),
            }
        if proc.returncode != 0:
            return {
                "name": "simulator",
                "ok": False,
                "detail": "simulator exited %s\n%s" % (proc.returncode, _tail(combined)),
            }
        if "Full traceback below" in combined:
            return {
                "name": "simulator",
                "ok": False,
                "detail": "user code raised an exception inside the simulator\n" + _tail(combined),
            }
        if "Sim time maxed out." not in stdout:
            return {
                "name": "simulator",
                "ok": False,
                "detail": "simulator ended before the time limit\n" + _tail(combined),
            }
        last_time = _last_sim_time(sim_pkg / "swarm_state_log.csv")
        if last_time is None or last_time < sim_seconds * 0.9:
            return {
                "name": "simulator",
                "ok": False,
                "detail": "state log did not reach %.0f simulated seconds (last=%s)"
                % (sim_seconds, last_time),
            }
        return {
            "name": "simulator",
            "ok": True,
            "detail": "headless run reached %.1f simulated seconds (USE_VIS=0, real-time factor %d)"
            % (last_time, REAL_TIME_FACTOR),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the pinned swarm simulator")
    parser.add_argument("command", choices=("fetch",))
    parser.add_argument("dest", type=Path)
    args = parser.parse_args(argv)
    try:
        path = ensure_simulator(args.dest)
    except (RuntimeError, subprocess.CalledProcessError) as err:
        print(str(err), file=sys.stderr)
        return 1
    print("simulator ready at %s (%s)" % (path, SIMULATOR_COMMIT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
