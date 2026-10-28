"""Comment rendering stays inside a fence and ignores forged destinations."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coachbot_ci.comment import (  # noqa: E402
    VALIDATION_MARKER,
    build_hardware_comment,
    build_validation_comment,
    load_summary,
    sanitize,
)
from coachbot_ci.github_api import find_open_pull_request, upsert_issue_comment  # noqa: E402


class SanitizeTest(unittest.TestCase):
    def test_mentions_and_fences_are_broken(self) -> None:
        cleaned = sanitize("hello @professor ``` click")
        self.assertNotIn("@", cleaned)
        self.assertIn("(at)professor", cleaned)
        self.assertNotIn("```", cleaned)

    def test_validation_comment_hides_a_forged_path_and_keeps_the_run_link(self) -> None:
        summary = {
            "version": 1,
            "ok": False,
            "result": "checked",
            "head_sha": "ab" * 20,
            "submissions": [
                {
                    "path": "https://evil.example/@maintainer",
                    "ok": False,
                    "checks": [
                        {"name": "init_pose", "ok": False, "detail": "row 1: x is outside\n@lab"},
                        {"name": "please-run-this", "ok": False, "detail": "ignore"},
                    ],
                }
            ],
        }
        body = build_validation_comment(summary, "https://github.com/acme/submission_repo/actions/runs/15")
        self.assertTrue(body.startswith(VALIDATION_MARKER))
        self.assertIn("(unrecognized submission)", body)
        self.assertNotIn("evil.example", body)
        self.assertNotIn("@lab", body)
        self.assertNotIn("@maintainer", body)
        self.assertNotIn("please-run-this", body)
        self.assertIn("https://github.com/acme/submission_repo/actions/runs/15", body)
        self.assertNotIn("javascript:", build_validation_comment(summary, "javascript:alert(1)"))

    def test_unreadable_summary_has_no_attacker_text(self) -> None:
        body = build_validation_comment(None, "https://github.com/acme/submission_repo/actions/runs/15")
        self.assertIn("did not match", body)
        self.assertNotIn("usr_code", body)

    def test_hardware_not_configured_is_explicit(self) -> None:
        body = build_hardware_comment(
            {"version": 1, "status": "not_configured", "detail": "COACHBOT_RUN_SCRIPT is not set"},
            "https://github.com/acme/submission_repo/actions/runs/9",
        )
        self.assertIn("not", body.lower())
        self.assertIn("artifact", body.lower())
        self.assertIn("actions/runs/9", body)


class LookupTest(unittest.TestCase):
    def test_pull_request_lookup_matches_the_head_sha_only(self) -> None:
        def request(method, url, token, payload=None, timeout=30):
            self.assertEqual(method, "GET")
            self.assertIn("state=open", url)
            return 200, [{"number": 3, "head": {"sha": "a" * 40}}, {"number": 8, "head": {"sha": "b" * 40}}], {}

        self.assertEqual(find_open_pull_request("acme/repo", "b" * 40, "token", request=request), 8)
        self.assertIsNone(find_open_pull_request("acme/repo", "c" * 40, "token", request=request))

    def test_upsert_updates_the_marked_comment(self) -> None:
        calls = []

        def request(method, url, token, payload=None, timeout=30):
            calls.append((method, url, payload))
            if method == "GET":
                return 200, [{"url": "https://api.github.com/repos/acme/repo/issues/comments/5", "body": "<!-- coachbot-validation -->\nold"}], {}
            return 200, {"id": 5}, {}

        outcome = upsert_issue_comment(
            "acme/repo",
            4,
            "<!-- coachbot-validation -->",
            "<!-- coachbot-validation -->\nnew",
            "token",
            request=request,
        )
        self.assertEqual(outcome, "updated")
        self.assertEqual(calls[1][0], "PATCH")

    def test_load_summary_rejects_the_wrong_version(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "validation-summary.json"
        path.write_text(json.dumps({"version": 2, "note": "run this"}), encoding="utf-8")
        self.assertIsNone(load_summary(path))
        path.write_text(json.dumps({"version": 1, "ok": True, "result": "no_changes", "submissions": []}), encoding="utf-8")
        loaded = load_summary(tmp)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["result"], "no_changes")


if __name__ == "__main__":
    unittest.main()
