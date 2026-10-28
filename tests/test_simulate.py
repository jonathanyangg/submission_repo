"""Headless simulator smoke test. Skipped unless SIMULATOR_DIR is set."""

from __future__ import annotations

import ast
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coachbot_ci.simulate import (  # noqa: E402
    REAL_TIME_FACTOR,
    simulation_config,
    simulator_env,
    run_headless_simulation,
)

SIMULATOR = os.environ.get("SIMULATOR_DIR", "").strip()


class ConfigTest(unittest.TestCase):
    def test_config_is_headless_and_hardware_sized(self) -> None:
        config = simulation_config(2, 60)
        self.assertEqual(config["USE_VIS"], 0)
        self.assertEqual(config["REAL_TIME_FACTOR"], REAL_TIME_FACTOR)
        self.assertEqual(config["SIM_TIME"], 60)
        self.assertEqual(config["NUMBER_OF_ROBOTS"], 2)
        self.assertEqual(config["MSG_SIZE"], 64)
        self.assertEqual(config["MSG_TYPE"], 1)

    def test_simulator_env_drops_tokens(self) -> None:
        previous = os.environ.get("GITHUB_TOKEN")
        os.environ["GITHUB_TOKEN"] = "secret"
        try:
            env = simulator_env()
        finally:
            if previous is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = previous
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["SDL_VIDEODRIVER"], "dummy")
        self.assertEqual(env["MPLBACKEND"], "Agg")


@unittest.skipUnless(SIMULATOR, "set SIMULATOR_DIR to the pinned swarm-simulator checkout")
class OrbitSmokeTest(unittest.TestCase):
    def test_example_orbit_moves_within_eight_simulated_seconds(self) -> None:
        log_dir = Path(tempfile.mkdtemp())
        result = run_headless_simulation(
            usr_code=ROOT / "submissions" / "example-orbit" / "usr_code.py",
            init_pose_text=(ROOT / "submissions" / "example-orbit" / "init_pose.csv").read_text(encoding="utf-8"),
            sim_seconds=8,
            simulator_dir=Path(SIMULATOR),
            log_dir=log_dir,
        )
        self.assertTrue(result["ok"], result["detail"])
        samples = []
        with (log_dir / "swarm_state_log.csv").open(newline="") as handle:
            for row in csv.reader(handle):
                if len(row) < 3:
                    continue
                try:
                    stamp = float(row[0])
                except ValueError:
                    continue
                state = ast.literal_eval(row[2].replace("np.float64(", "").replace(")", ""))
                samples.append((stamp, state))
        self.assertGreater(len(samples), 5)
        for _stamp, state in samples:
            self.assertLess(abs(state[0][0]), 0.05)
            self.assertLess(abs(state[0][1]), 0.05)
        later = [state for stamp, state in samples if stamp > 5]
        self.assertTrue(later)
        distances = [(state[1][0] ** 2 + state[1][1] ** 2) ** 0.5 for state in later]
        self.assertLess(min(distances), 0.78)


if __name__ == "__main__":
    unittest.main()
