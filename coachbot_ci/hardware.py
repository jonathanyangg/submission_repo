"""Call the lab's private robot-run script, or record that it is not configured.

The real script stays on the lab computer. This module is the contract the
workflow calls after a maintainer approves the swarm environment. It does not
import usr_code.py and it does not start a simulator.

    $COACHBOT_RUN_SCRIPT \
        --submission-dir <directory> \
        --output-dir <directory> \
        --timeout-sec 600

timeout-sec is the user-code cap the lab script should enforce, matching the
10 minute limit in the user guide. This process uses a separate, longer
wall-clock limit so positioning and charging can finish, then kills the script
if it is still running.

The child environment drops GitHub Actions tokens and any variable whose
name contains TOKEN, SECRET, PASSWORD, PASSWD, or CREDENTIAL. Names listed
in COACHBOT_DROP_ENV (comma-separated) are removed as well.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from coachbot_ci.github_api import list_pull_request_paths
from coachbot_ci.validate import SUBMISSION_NAME_RE, submission_dirs_in

RESULT_NAME = "hardware-result.json"
NOT_CONFIGURED_NAME = "NOT_CONFIGURED.txt"
USER_CODE_TIMEOUT_SEC = 600
WALL_TIMEOUT_SEC = 2400
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TOKEN_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
ALWAYS_DROP = {
    "GITHUB_TOKEN",
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_RESULTS_URL",
    "COACHBOT_RUN_SCRIPT",
    "COACHBOT_DROP_ENV",
}


def scrub_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Copy source with credential-shaped variables removed."""
    env = dict(os.environ if source is None else source)
    extra = [part.strip() for part in env.get("COACHBOT_DROP_ENV", "").split(",") if part.strip()]
    drop = set(ALWAYS_DROP)
    drop.update(extra)
    for name in list(env):
        upper = name.upper()
        if name in drop or any(marker in upper for marker in TOKEN_MARKERS):
            del env[name]
    return env


def _result(status: str, detail: str, exit_code: int) -> dict[str, object]:
    return {"version": 1, "status": status, "detail": detail, "exit_code": exit_code}


def _write_result(output_dir: Path, result: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / RESULT_NAME).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def _check_submission_dir(submission_dir: Path) -> None:
    resolved = submission_dir.resolve()
    if not resolved.is_dir():
        raise ValueError("submission directory does not exist: %s" % submission_dir)
    if not SUBMISSION_NAME_RE.fullmatch(resolved.name) or resolved.parent.name != "submissions":
        raise ValueError("submission directory must be submissions/<name>")
    for name in ("usr_code.py", "init_pose.csv"):
        path = resolved / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("%s must be a regular file" % name)


def run_configured_script(
    submission_dir: Path,
    output_dir: Path,
    user_code_timeout_sec: int = USER_CODE_TIMEOUT_SEC,
    wall_timeout_sec: int = WALL_TIMEOUT_SEC,
    environ: dict[str, str] | None = None,
) -> dict[str, object]:
    """Invoke COACHBOT_RUN_SCRIPT, or write a not-configured result.

    The returned dict is also written to output_dir/hardware-result.json.
    Exit status is the dict's exit_code. 0 means the lab script exited 0.
    2 means the runner has no script configured.
    """
    env = dict(os.environ if environ is None else environ)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _check_submission_dir(submission_dir)
    except ValueError as err:
        result = _result("failed", str(err), 1)
        _write_result(output_dir, result)
        return result

    raw_script = env.get("COACHBOT_RUN_SCRIPT", "").strip()
    if not raw_script:
        notice = (
            "COACHBOT_RUN_SCRIPT is not set, so the robots were not started.\n"
            "On the lab computer, point that variable at the private run script.\n"
            "The workflow calls it as:\n"
            "  $COACHBOT_RUN_SCRIPT --submission-dir DIR --output-dir DIR --timeout-sec %d\n"
            "--timeout-sec is the user-code cap. This placeholder does not implement\n"
            "charging, positioning, or the robot runtime.\n" % user_code_timeout_sec
        )
        (output_dir / NOT_CONFIGURED_NAME).write_text(notice, encoding="utf-8")
        result = _result("not_configured", notice.strip(), 2)
        _write_result(output_dir, result)
        return result

    script = Path(raw_script)
    if not script.is_absolute():
        result = _result("failed", "COACHBOT_RUN_SCRIPT must be an absolute path", 1)
        _write_result(output_dir, result)
        return result
    try:
        resolved_script = script.resolve()
    except OSError as err:
        result = _result("failed", "could not resolve the lab script: %s" % err, 1)
        _write_result(output_dir, result)
        return result
    submission = submission_dir.resolve()
    if resolved_script == submission or submission in resolved_script.parents:
        result = _result("failed", "the lab script must not live inside the submission", 1)
        _write_result(output_dir, result)
        return result
    if not resolved_script.is_file():
        result = _result("failed", "the configured lab script is not a file", 1)
        _write_result(output_dir, result)
        return result

    argv = [
        str(resolved_script),
        "--submission-dir",
        str(submission),
        "--output-dir",
        str(output_dir.resolve()),
        "--timeout-sec",
        str(user_code_timeout_sec),
    ]
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=wall_timeout_sec,
            env=scrub_environment(env),
        )
    except subprocess.TimeoutExpired:
        result = _result(
            "timed_out",
            "the lab script was still running after %d seconds" % wall_timeout_sec,
            124,
        )
        _write_result(output_dir, result)
        return result
    except OSError as err:
        result = _result("failed", "could not start the lab script: %s" % err, 1)
        _write_result(output_dir, result)
        return result

    detail = "lab script exited %d" % completed.returncode
    tail = (completed.stderr or completed.stdout or "").strip()
    if tail:
        detail = detail + "\n" + tail[-2000:]
    status = "ok" if completed.returncode == 0 else "failed"
    result = _result(status, detail, completed.returncode)
    _write_result(output_dir, result)
    return result


def resolve_hardware_target(
    event: dict[str, object],
    environ: dict[str, str],
    list_files=list_pull_request_paths,
) -> dict[str, str]:
    """Decide which commit and folder a hardware job should read.

    workflow_dispatch reads an explicit submissions/<name> and an optional
    40-character git ref. pull_request_target is only accepted for the
    hardware-run label and only when the pull request touches exactly one
    submission folder. Values are checked before they are handed to checkout.
    """
    event_name = environ.get("GITHUB_EVENT_NAME", "")
    repository = environ.get("GITHUB_REPOSITORY", "")
    sha = environ.get("GITHUB_SHA", "")
    if event_name == "workflow_dispatch":
        inputs = event.get("inputs")
        if not isinstance(inputs, dict):
            inputs = {}
        requested_repo = str(inputs.get("repository") or "").strip() or repository
        requested_ref = str(inputs.get("git_ref") or "").strip() or sha
        submission_dir = str(inputs.get("submission_dir") or "").strip().strip("/")
        pr_number = str(inputs.get("pr_number") or "").strip()
        if not REPO_RE.fullmatch(requested_repo):
            raise ValueError("repository must look like owner/name")
        if not SHA_RE.fullmatch(requested_ref):
            raise ValueError("git_ref must be a 40-character commit sha")
        parts = submission_dir.split("/")
        if len(parts) != 2 or parts[0] != "submissions" or not SUBMISSION_NAME_RE.fullmatch(parts[1]):
            raise ValueError("submission_dir must look like submissions/<name>")
        if pr_number and not pr_number.isdigit():
            raise ValueError("pr_number must be digits")
        return {
            "repository": requested_repo,
            "ref": requested_ref,
            "submission_dir": submission_dir,
            "pr_number": pr_number,
        }

    if event_name != "pull_request_target":
        raise ValueError("unsupported event %s" % event_name)
    label = event.get("label")
    label_name = label.get("name") if isinstance(label, dict) else ""
    if label_name != "hardware-run":
        raise ValueError("refusing to continue without the hardware-run label")
    pull = event.get("pull_request")
    if not isinstance(pull, dict):
        raise ValueError("pull request payload is missing")
    number = pull.get("number")
    head = pull.get("head")
    if not isinstance(number, int) or not isinstance(head, dict):
        raise ValueError("pull request payload is incomplete")
    head_sha = head.get("sha")
    head_repo = head.get("repo")
    full_name = head_repo.get("full_name") if isinstance(head_repo, dict) else ""
    if not isinstance(head_sha, str) or not SHA_RE.fullmatch(head_sha):
        raise ValueError("pull request head is not a commit sha")
    if not isinstance(full_name, str) or not REPO_RE.fullmatch(full_name):
        raise ValueError("pull request head repository is not owner/name")
    if not REPO_RE.fullmatch(repository):
        raise ValueError("GITHUB_REPOSITORY is not owner/name")
    token = environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise ValueError("GITHUB_TOKEN is required to list pull request files")
    paths = list_files(repository, number, token)
    folders = submission_dirs_in(paths)
    if len(folders) != 1:
        raise ValueError(
            "the hardware-run label needs exactly one submissions/<name> directory in the pull request; found %s"
            % (folders or "none")
        )
    return {
        "repository": full_name,
        "ref": head_sha,
        "submission_dir": folders[0],
        "pr_number": str(number),
    }


def _write_github_output(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError("refusing to write a multiline output")
            handle.write("%s=%s\n" % (key, value))


def _cmd_run(args: argparse.Namespace) -> int:
    result = run_configured_script(
        submission_dir=args.submission_dir,
        output_dir=args.output_dir,
        user_code_timeout_sec=args.user_code_timeout_sec,
        wall_timeout_sec=args.wall_timeout_sec,
    )
    print(result["status"])
    return int(result["exit_code"])


def _cmd_resolve(_args: argparse.Namespace) -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        print("GITHUB_EVENT_PATH is not set", file=sys.stderr)
        return 1
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        target = resolve_hardware_target(event, dict(os.environ))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as err:
        print(str(err), file=sys.stderr)
        return 1
    _write_github_output(target)
    for key, value in target.items():
        print("%s=%s" % (key, value))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coachbot hardware-run placeholder")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="call the lab script or record that it is unset")
    run.add_argument("--submission-dir", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--user-code-timeout-sec", type=int, default=USER_CODE_TIMEOUT_SEC)
    run.add_argument("--wall-timeout-sec", type=int, default=WALL_TIMEOUT_SEC)
    run.set_defaults(func=_cmd_run)
    resolve = sub.add_parser("resolve", help="write the checkout target to GITHUB_OUTPUT")
    resolve.set_defaults(func=_cmd_resolve)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
