"""Turn a validation or hardware result into one pull-request comment.

Text that originated in a pull request is treated as untrusted. Check details
are forced into a code fence after control characters, @-mentions, and fence
breaks are removed. The run URL is accepted only when it points at a GitHub
Actions run. The pull request number comes from the event or from a lookup of
the head commit, never from the uploaded summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from coachbot_ci.github_api import SHA_RE, find_open_pull_request, upsert_issue_comment
from coachbot_ci.validate import SUBMISSION_NAME_RE

VALIDATION_MARKER = "<!-- coachbot-validation -->"
HARDWARE_MARKER = "<!-- coachbot-hardware -->"
RUN_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+$"
)
ALLOWED_CHECKS = ("files", "init_pose", "python3_syntax", "python27_syntax", "simulator")
MAX_SUMMARY_BYTES = 200_000


def sanitize(text: object, limit: int = 800) -> str:
    """Reduce untrusted text to a short ASCII snippet that cannot ping anyone."""
    raw = text if isinstance(text, str) else ""
    kept: list[str] = []
    for char in raw:
        if char in "\n\t" or 32 <= ord(char) < 127:
            kept.append(char)
    cleaned = "".join(kept).replace("@", "(at)").replace("```", "'''")
    cleaned = cleaned.strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "\n(truncated)"
    return cleaned


def clean_run_url(url: str) -> str:
    if RUN_URL_RE.fullmatch(url.strip()):
        return url.strip()
    return ""


def _fence(text: str) -> str:
    body = sanitize(text, limit=1200)
    if not body:
        return ""
    return "```\n%s\n```" % body


def _submission_label(path: object) -> str:
    if not isinstance(path, str):
        return "(unrecognized submission)"
    parts = path.strip("/").split("/")
    if len(parts) == 2 and parts[0] == "submissions" and SUBMISSION_NAME_RE.fullmatch(parts[1]):
        return path.strip("/")
    return "(unrecognized submission)"


def locate_summary(path: Path) -> Path | None:
    """Find validation-summary.json at path or one directory level down.

    upload-artifact keeps the uploaded directory in some layouts and flattens
    it in others. Exactly one match is accepted.
    """
    if path.is_file():
        return path
    if not path.is_dir():
        return None
    matches = [item for item in path.rglob("validation-summary.json") if item.is_file()]
    if len(matches) == 1:
        return matches[0]
    return None


def load_summary(path: Path) -> dict[str, object] | None:
    """Load a version-1 summary, or return None when it should not be rendered."""
    located = locate_summary(path)
    if located is None:
        return None
    try:
        data = located.read_bytes()[: MAX_SUMMARY_BYTES + 1]
    except OSError:
        return None
    if len(data) > MAX_SUMMARY_BYTES:
        return None
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or parsed.get("version") != 1:
        return None
    return parsed


def build_validation_comment(summary: dict[str, object] | None, run_url: str) -> str:
    """Build the sticky validation comment. summary may be untrusted."""
    url = clean_run_url(run_url)
    lines = [VALIDATION_MARKER, "### Coachbot validation", ""]
    if summary is None:
        lines.append("The validation summary was missing or did not match the expected format, so check details are omitted.")
    elif summary.get("result") == "no_changes":
        lines.append("No files under `submissions/` changed, so pose and simulation checks were not run.")
    elif summary.get("result") == "error" or summary.get("ok") is not True and not summary.get("submissions"):
        lines.append("Validation could not finish.")
        note = _fence(str(summary.get("note") or ""))
        if note:
            lines.extend(["", note])
    else:
        submissions = summary.get("submissions")
        if not isinstance(submissions, list) or not submissions:
            lines.append("Validation produced no submission result.")
        else:
            passed = summary.get("ok") is True
            lines.append("Result: %s." % ("passed" if passed else "failed"))
            head = summary.get("head_sha")
            if isinstance(head, str) and SHA_RE.fullmatch(head):
                lines.append("Commit: `%s`" % head)
            for item in submissions[:8]:
                if not isinstance(item, dict):
                    continue
                label = _submission_label(item.get("path"))
                lines.extend(["", "#### `%s`" % label, ""])
                lines.append("Status: %s." % ("passed" if item.get("ok") is True else "failed"))
                checks = item.get("checks")
                if not isinstance(checks, list):
                    continue
                for check in checks:
                    if not isinstance(check, dict) or check.get("name") not in ALLOWED_CHECKS:
                        continue
                    state = "ok" if check.get("ok") is True else "failed"
                    lines.append("- %s: %s" % (check["name"], state))
                    fenced = _fence(check.get("detail"))
                    if fenced and check.get("ok") is not True:
                        lines.extend(["", fenced])
    lines.extend(["", "Logs are the `validation-logs` artifact on this run."])
    if url:
        lines.append(url)
    lines.extend(
        [
            "",
            "This check does not start the robots. A maintainer adds the `hardware-run` label and approves the `swarm` environment to queue the lab computer.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_hardware_comment(result: dict[str, object] | None, run_url: str) -> str:
    """Build the hardware-run comment from the stub's own result file."""
    url = clean_run_url(run_url)
    lines = [HARDWARE_MARKER, "### Coachbot hardware run", ""]
    if result is None or result.get("version") != 1:
        lines.append("The hardware result file was missing, so no status is shown.")
    else:
        status = result.get("status")
        if status == "ok":
            lines.append("The lab script finished and its files are in the `hardware-results` artifact.")
        elif status == "not_configured":
            lines.append(
                "The lab runner has no `COACHBOT_RUN_SCRIPT` configured, so the robots were not started. The artifact explains the script contract."
            )
        elif status == "timed_out":
            lines.append("The lab script was still running when the wall-clock limit expired. The robots may need a manual check.")
        else:
            lines.append("The lab script did not finish successfully.")
        fenced = _fence(result.get("detail"))
        if fenced and status != "ok":
            lines.extend(["", fenced])
    lines.extend(["", "Results are uploaded as an Actions artifact and are not committed to the repository."])
    if url:
        lines.append(url)
    return "\n".join(lines) + "\n"


def _resolve_pr(args: argparse.Namespace) -> int | None:
    if args.pr.strip():
        if not args.pr.strip().isdigit():
            raise ValueError("pull request number must be digits")
        return int(args.pr.strip())
    if args.head_sha.strip():
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            raise ValueError("GITHUB_TOKEN is required to find the pull request")
        return find_open_pull_request(args.repo, args.head_sha.strip(), token)
    return None


def _post(marker: str, body: str, args: argparse.Namespace) -> int:
    try:
        number = _resolve_pr(args)
    except (ValueError, RuntimeError) as err:
        print(str(err), file=sys.stderr)
        return 1
    if number is None:
        print("no open pull request to comment on")
        return 0
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("GITHUB_TOKEN is not set; skipping the comment")
        return 0
    try:
        outcome = upsert_issue_comment(args.repo, number, marker, body, token)
    except (ValueError, RuntimeError) as err:
        print(str(err), file=sys.stderr)
        return 1
    print(outcome)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Comment on a Coachbot pull request")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr", default=os.environ.get("PR_NUMBER", ""))
    parser.add_argument("--head-sha", default=os.environ.get("PR_HEAD_SHA", ""))
    parser.add_argument("--run-url", default=os.environ.get("RUN_URL", ""))
    sub = parser.add_subparsers(dest="command", required=True)

    validation = sub.add_parser("validation")
    validation.add_argument("--summary", type=Path, required=True)

    hardware = sub.add_parser("hardware")
    hardware.add_argument("--result", type=Path, required=True)

    args = parser.parse_args(argv)
    if not args.repo:
        print("repository is required", file=sys.stderr)
        return 1
    if args.command == "validation":
        summary = load_summary(args.summary) if args.summary.exists() else None
        body = build_validation_comment(summary, args.run_url)
        return _post(VALIDATION_MARKER, body, args)
    result = None
    if args.result.is_file():
        try:
            loaded = json.loads(args.result.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            result = loaded
    body = build_hardware_comment(result, args.run_url)
    return _post(HARDWARE_MARKER, body, args)


if __name__ == "__main__":
    sys.exit(main())
