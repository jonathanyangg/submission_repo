"""The lab-script placeholder and the hardware target resolver."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coachbot_ci.hardware import (  # noqa: E402
    resolve_hardware_target,
    run_configured_script,
    scrub_environment,
)


class ScrubTest(unittest.TestCase):
    def test_tokens_are_removed_and_path_remains(self) -> None:
        cleaned = scrub_environment(
            {
                "PATH": "/usr/bin",
                "GITHUB_TOKEN": "secret",
                "ACTIONS_RUNTIME_TOKEN": "secret",
                "LAB_PASSWORD": "secret",
                "COACHBOT_DROP_ENV": "CUSTOM_CREDENTIAL",
                "CUSTOM_CREDENTIAL": "secret",
                "COACHBOT_RUN_SCRIPT": "/opt/coachbot/run",
                "HOME": "/home/lab",
            }
        )
        self.assertEqual(cleaned["PATH"], "/usr/bin")
        self.assertEqual(cleaned["HOME"], "/home/lab")
        for name in ("GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN", "LAB_PASSWORD", "CUSTOM_CREDENTIAL", "COACHBOT_RUN_SCRIPT"):
            self.assertNotIn(name, cleaned)


class RunScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.submission = self.tmp / "submissions" / "demo"
        self.submission.mkdir(parents=True)
        (self.submission / "usr_code.py").write_text("def usr(robot):\n    pass\n", encoding="utf-8")
        (self.submission / "init_pose.csv").write_text("0,0,0,0\n", encoding="utf-8")
        self.output = self.tmp / "out"

    def test_missing_script_does_not_pretend_to_run(self) -> None:
        result = run_configured_script(self.submission, self.output, environ={"PATH": "/usr/bin"})
        self.assertEqual(result["status"], "not_configured")
        self.assertEqual(result["exit_code"], 2)
        self.assertTrue((self.output / "NOT_CONFIGURED.txt").is_file())
        self.assertIn("--timeout-sec", (self.output / "NOT_CONFIGURED.txt").read_text(encoding="utf-8"))

    def test_script_receives_the_contract_and_not_the_token(self) -> None:
        script = self.tmp / "lab_run.py"
        script.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json, os, pathlib, sys",
                    "out = pathlib.Path(sys.argv[sys.argv.index('--output-dir') + 1])",
                    "payload = {'argv': sys.argv[1:], 'token': os.environ.get('GITHUB_TOKEN')}",
                    "(out / 'called.json').write_text(json.dumps(payload))",
                    "(out / 'camera_video.mp4').write_bytes(b'not-a-video')",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        result = run_configured_script(
            self.submission,
            self.output,
            user_code_timeout_sec=600,
            environ={
                "PATH": os.environ["PATH"],
                "COACHBOT_RUN_SCRIPT": str(script),
                "GITHUB_TOKEN": "should-not-leak",
            },
        )
        self.assertEqual(result["exit_code"], 0, result)
        called = json.loads((self.output / "called.json").read_text(encoding="utf-8"))
        self.assertEqual(called["token"], None)
        self.assertIn("--submission-dir", called["argv"])
        self.assertIn(str(self.submission.resolve()), called["argv"])
        self.assertIn("--timeout-sec", called["argv"])
        self.assertIn("600", called["argv"])
        self.assertTrue((self.output / "camera_video.mp4").is_file())

    def test_script_inside_the_submission_is_refused(self) -> None:
        planted = self.submission / "run.sh"
        planted.write_text("#!/bin/sh\n", encoding="utf-8")
        result = run_configured_script(
            self.submission,
            self.output,
            environ={"PATH": "/usr/bin", "COACHBOT_RUN_SCRIPT": str(planted)},
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("must not live inside", str(result["detail"]))

    def test_symlink_user_code_is_refused(self) -> None:
        outside = self.tmp / "elsewhere.py"
        outside.write_text("def usr(robot):\n    pass\n", encoding="utf-8")
        (self.submission / "usr_code.py").unlink()
        (self.submission / "usr_code.py").symlink_to(outside)
        result = run_configured_script(
            self.submission,
            self.output,
            environ={"PATH": "/bin", "COACHBOT_RUN_SCRIPT": "/bin/true"},
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("regular file", str(result["detail"]))


class ResolveTest(unittest.TestCase):
    def test_dispatch_requires_a_sha_and_one_folder(self) -> None:
        event = {
            "inputs": {
                "submission_dir": "submissions/example-orbit",
                "git_ref": "a" * 40,
                "repository": "Coachbot-Swarm/submission_repo",
                "pr_number": "12",
            }
        }
        target = resolve_hardware_target(
            event,
            {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": "ignored/ignored", "GITHUB_SHA": "b" * 40},
        )
        self.assertEqual(target["repository"], "Coachbot-Swarm/submission_repo")
        self.assertEqual(target["ref"], "a" * 40)
        self.assertEqual(target["submission_dir"], "submissions/example-orbit")
        self.assertEqual(target["pr_number"], "12")

    def test_label_uses_the_single_changed_folder(self) -> None:
        event = {
            "label": {"name": "hardware-run"},
            "pull_request": {
                "number": 4,
                "head": {"sha": "c" * 40, "repo": {"full_name": "student/submission_repo"}},
            },
        }

        def list_files(repo: str, number: int, token: str) -> list[str]:
            self.assertEqual(repo, "Coachbot-Swarm/submission_repo")
            self.assertEqual(number, 4)
            self.assertEqual(token, "token")
            return ["submissions/demo/usr_code.py", "submissions/demo/init_pose.csv"]

        target = resolve_hardware_target(
            event,
            {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_REPOSITORY": "Coachbot-Swarm/submission_repo",
                "GITHUB_SHA": "d" * 40,
                "GITHUB_TOKEN": "token",
            },
            list_files=list_files,
        )
        self.assertEqual(target["repository"], "student/submission_repo")
        self.assertEqual(target["ref"], "c" * 40)
        self.assertEqual(target["submission_dir"], "submissions/demo")

    def test_two_folders_are_rejected(self) -> None:
        event = {
            "label": {"name": "hardware-run"},
            "pull_request": {
                "number": 4,
                "head": {"sha": "c" * 40, "repo": {"full_name": "student/submission_repo"}},
            },
        }
        with self.assertRaises(ValueError):
            resolve_hardware_target(
                event,
                {
                    "GITHUB_EVENT_NAME": "pull_request_target",
                    "GITHUB_REPOSITORY": "Coachbot-Swarm/submission_repo",
                    "GITHUB_TOKEN": "token",
                },
                list_files=lambda _repo, _number, _token: [
                    "submissions/one/usr_code.py",
                    "submissions/two/usr_code.py",
                ],
            )


if __name__ == "__main__":
    unittest.main()
