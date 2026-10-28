"""Pose rules, file checks, and Python 2.7 syntax."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coachbot_ci.validate import (  # noqa: E402
    MAX_ROBOTS,
    build_summary,
    check_syntax,
    select_submissions,
    submission_dirs_in,
    validate_folder,
    validate_init_pose,
)


def _grid(count: int) -> str:
    xs = [-1.2 + 0.3 * i for i in range(8)]
    ys = [-1.4 + 0.3 * i for i in range(7)]
    points = [(x, y) for y in ys for x in xs]
    lines = ["%d,%.3f,%.3f,0" % (index, x, y) for index, (x, y) in enumerate(points[:count])]
    return "\n".join(lines) + "\n"


class PoseRulesTest(unittest.TestCase):
    def test_example_file_passes(self) -> None:
        text = (ROOT / "submissions" / "example-orbit" / "init_pose.csv").read_text(encoding="utf-8")
        self.assertEqual(validate_init_pose(text), [])

    def test_bounds_are_inclusive_and_just_outside_fails(self) -> None:
        self.assertEqual(validate_init_pose("0,-1.2,-1.4,0\n"), [])
        self.assertEqual(validate_init_pose("0,1.0,2.35,0\n"), [])
        self.assertTrue(any("x=" in error for error in validate_init_pose("0,1.01,0,0\n")))
        self.assertTrue(any("x=" in error for error in validate_init_pose("0,-1.21,0,0\n")))
        self.assertTrue(any("y=" in error for error in validate_init_pose("0,0,2.36,0\n")))
        self.assertTrue(any("y=" in error for error in validate_init_pose("0,0,-1.41,0\n")))

    def test_separation_boundary(self) -> None:
        self.assertEqual(validate_init_pose("0,0,0,0\n1,0.25,0,0\n"), [])
        errors = validate_init_pose("0,0,0,0\n1,0.249,0,0\n")
        self.assertTrue(any("0.25" in error for error in errors))

    def test_ids_and_count(self) -> None:
        self.assertEqual(validate_init_pose(_grid(MAX_ROBOTS)), [])
        self.assertTrue(any("outside" in error for error in validate_init_pose("50,0,0,0\n")))
        self.assertTrue(any("outside" in error for error in validate_init_pose("-1,0,0,0\n")))
        self.assertTrue(any("whole number" in error for error in validate_init_pose("1.5,0,0,0\n")))
        errors = validate_init_pose("0,0,0,0\n0,0.5,0,0\n")
        self.assertTrue(any("repeated" in error for error in errors))
        too_many = _grid(50) + "0,0.9,2.0,0\n"
        self.assertTrue(any("maximum" in error for error in validate_init_pose(too_many)))

    def test_theta_interval_is_open_around_two_pi(self) -> None:
        almost = 2.0 * math.pi - 0.001
        self.assertEqual(validate_init_pose("0,0,0,%s\n" % almost), [])
        errors = validate_init_pose("0,0,0,%s\n" % (2.0 * math.pi))
        self.assertTrue(any("theta" in error for error in errors))

    def test_blank_header_and_empty_file(self) -> None:
        self.assertTrue(validate_init_pose(""))
        self.assertTrue(any("columns" in error or "not a number" in error for error in validate_init_pose("id,x,y,theta\n")))
        self.assertTrue(any("blank" in error for error in validate_init_pose("0,0,0,0\n\n")))


class SyntaxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, source: str) -> Path:
        path = self.tmp / "usr_code.py"
        path.write_text(source, encoding="utf-8")
        return path

    def test_python3_rejects_print_statement(self) -> None:
        result = check_syntax(self._write('def usr(robot):\n    print "hi"\n'), "python3")
        self.assertFalse(result["ok"])
        self.assertIn("syntax", str(result["detail"]).lower())

    def test_python2_rejects_fstring_when_available(self) -> None:
        if not os.environ.get("PY2_BIN") and shutil.which("python2.7") is None and shutil.which("docker") is None:
            self.skipTest("Python 2.7 and Docker are both unavailable")
        result = check_syntax(self._write('def usr(robot):\n    name = f"robot"\n'), "python2")
        self.assertFalse(result["ok"])
        self.assertIn("syntax", str(result["detail"]).lower())

    def test_example_parses_on_python3_and_python2(self) -> None:
        if not os.environ.get("PY2_BIN") and shutil.which("python2.7") is None and shutil.which("docker") is None:
            self.skipTest("Python 2.7 and Docker are both unavailable")
        path = ROOT / "submissions" / "example-orbit" / "usr_code.py"
        py3 = check_syntax(path, "python3")
        py2 = check_syntax(path, "python2")
        self.assertTrue(py3["ok"], py3["detail"])
        self.assertTrue(py2["ok"], py2["detail"])

    def test_missing_usr_fails(self) -> None:
        result = check_syntax(self._write("def user(robot):\n    return robot\n"), "python3")
        self.assertFalse(result["ok"])
        self.assertIn("usr", str(result["detail"]))


class SelectionTest(unittest.TestCase):
    def test_only_nested_submission_files_count(self) -> None:
        paths = [
            "README.md",
            "submissions/README.md",
            "submissions/example-orbit/usr_code.py",
            "submissions/../usr_code.py",
            "submissions/.hidden/usr_code.py",
        ]
        self.assertEqual(submission_dirs_in(paths), ["submissions/example-orbit"])

    def test_explicit_directory_and_diff(self) -> None:
        folders, note = select_submissions(ROOT, "submissions/example-orbit", "", "")
        self.assertEqual(folders, ["submissions/example-orbit"])
        self.assertEqual(note, "")
        with self.assertRaises(ValueError):
            select_submissions(ROOT, "usr_code.py", "", "")

    def test_diff_finds_a_new_folder(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )
        subprocess.check_call(["git", "-c", "init.defaultBranch=main", "init"], cwd=tmp, env=env)
        (tmp / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "README.md"], cwd=tmp, env=env)
        subprocess.check_call(["git", "commit", "-m", "base"], cwd=tmp, env=env)
        base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp, text=True).strip()
        folder = tmp / "submissions" / "demo"
        folder.mkdir(parents=True)
        (folder / "usr_code.py").write_text("def usr(robot):\n    pass\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "submissions"], cwd=tmp, env=env)
        subprocess.check_call(["git", "commit", "-m", "add demo"], cwd=tmp, env=env)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp, text=True).strip()
        folders, note = select_submissions(tmp, "", base, head)
        self.assertEqual(folders, ["submissions/demo"])
        self.assertEqual(note, "")
        unchanged, unchanged_note = select_submissions(tmp, "", base, base)
        self.assertEqual(unchanged, [])
        self.assertIn("No files under submissions/", unchanged_note)


class FolderTest(unittest.TestCase):
    def test_example_static_checks_pass(self) -> None:
        if not os.environ.get("PY2_BIN") and shutil.which("python2.7") is None and shutil.which("docker") is None:
            self.skipTest("Python 2.7 and Docker are both unavailable")
        result = validate_folder(
            ROOT / "submissions" / "example-orbit",
            sim_seconds=60,
            simulator_dir=None,
            log_dir=None,
            run_simulator=False,
        )
        self.assertTrue(result["ok"], result)
        names = [check["name"] for check in result["checks"]]
        self.assertEqual(
            names,
            ["files", "init_pose", "python3_syntax", "python27_syntax", "simulator"],
        )

    def test_bad_sim_seconds_become_an_error_summary(self) -> None:
        previous = os.environ.get("SIM_SECONDS")
        os.environ["SIM_SECONDS"] = "nope"
        try:
            summary = build_summary(ROOT)
        finally:
            if previous is None:
                os.environ.pop("SIM_SECONDS", None)
            else:
                os.environ["SIM_SECONDS"] = previous
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["result"], "error")


if __name__ == "__main__":
    unittest.main()
