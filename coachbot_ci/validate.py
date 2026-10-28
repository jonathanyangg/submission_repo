"""Check a Coachbot submission folder without running it on the robots.

A submission is a directory under submissions/ with usr_code.py and
init_pose.csv. Pose rules match the Coachbot user guide (November 2025).
Python 2.7 syntax is required because the robots run 2.7.16. Python 3
syntax is also required because the swarm simulator imports the file.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from coachbot_ci.simulate import run_headless_simulation

FIELD_X_MIN = -1.2
FIELD_X_MAX = 1.0
FIELD_Y_MIN = -1.4
FIELD_Y_MAX = 2.35
MIN_SEPARATION_M = 0.25
MIN_ROBOT_ID = 0
MAX_ROBOT_ID = 49
MAX_ROBOTS = 50
THETA_LIMIT = 2.0 * math.pi
SEPARATION_TOLERANCE_M = 1e-6
MAX_FILE_BYTES = 512 * 1024
MAX_SUBMISSIONS = 8
DEFAULT_SIM_SECONDS = 60
MAX_SIM_SECONDS = 120
SUBMISSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
NO_CHANGES_NOTE = (
    "No files under submissions/ changed, so pose and simulation checks were not run."
)
SUMMARY_VERSION = 1

PYTHON2_IMAGE = "python:2.7.18-slim"


def submission_dirs_in(paths: Iterable[str]) -> list[str]:
    """Return submissions/<name> directories touched by a list of file paths.

    A file has to live inside the directory (submissions/<name>/<file>).
    The submissions/README.md file at the top of that folder is not a
    submission. Names that could escape the folder, such as "..", are dropped.
    """
    found: list[str] = []
    seen: set[str] = set()
    for path in paths:
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "submissions":
            continue
        name = parts[1]
        if not SUBMISSION_NAME_RE.fullmatch(name):
            continue
        folder = "submissions/" + name
        if folder not in seen:
            seen.add(folder)
            found.append(folder)
    return found


def _finite_float(cell: str, label: str) -> tuple[float | None, str | None]:
    try:
        value = float(cell.strip())
    except ValueError:
        return None, "%s is not a number" % label
    if not math.isfinite(value):
        return None, "%s is not a finite number" % label
    return value, None


def _robot_id(cell: str) -> tuple[int | None, str | None]:
    value, error = _finite_float(cell, "id")
    if error:
        return None, error
    assert value is not None
    nearest = int(round(value))
    if abs(value - nearest) > 1e-6:
        return None, "id %s is not a whole number" % cell.strip()
    if nearest < MIN_ROBOT_ID or nearest > MAX_ROBOT_ID:
        return None, "id %d is outside %d..%d" % (nearest, MIN_ROBOT_ID, MAX_ROBOT_ID)
    return nearest, None


def validate_init_pose(text: str) -> list[str]:
    """Return pose-rule errors for an init_pose.csv body. Empty means valid.

    Rules from the user guide: whole-number ids 0 through 49, at most 50
    robots, x in [-1.2, 1.0] m, y in [-1.4, 2.35] m, theta in (-2π, 2π),
    and at least 0.25 m between every pair.
    """
    errors: list[str] = []
    rows: list[tuple[int, int, float, float, float]] = []
    reader = csv.reader(io.StringIO(text))
    saw_data = False
    for line_number, row in enumerate(reader, start=1):
        if not row or all(not cell.strip() for cell in row):
            errors.append("row %d is blank; delete the blank line" % line_number)
            continue
        saw_data = True
        if len(row) != 4:
            errors.append("row %d has %d columns; expected id,x,y,theta" % (line_number, len(row)))
            continue
        robot_id, id_error = _robot_id(row[0])
        x_value, x_error = _finite_float(row[1], "x")
        y_value, y_error = _finite_float(row[2], "y")
        theta_value, theta_error = _finite_float(row[3], "theta")
        for message in (id_error, x_error, y_error, theta_error):
            if message:
                errors.append("row %d: %s" % (line_number, message))
        if None in (robot_id, x_value, y_value, theta_value):
            continue
        assert robot_id is not None and x_value is not None
        assert y_value is not None and theta_value is not None
        if x_value < FIELD_X_MIN or x_value > FIELD_X_MAX:
            errors.append(
                "row %d: x=%.4f m is outside [%.1f, %.1f]"
                % (line_number, x_value, FIELD_X_MIN, FIELD_X_MAX)
            )
        if y_value < FIELD_Y_MIN or y_value > FIELD_Y_MAX:
            errors.append(
                "row %d: y=%.4f m is outside [%.2f, %.2f]"
                % (line_number, y_value, FIELD_Y_MIN, FIELD_Y_MAX)
            )
        if not (-THETA_LIMIT < theta_value < THETA_LIMIT):
            errors.append(
                "row %d: theta=%.4f rad is outside (-2π, 2π)" % (line_number, theta_value)
            )
        rows.append((line_number, robot_id, x_value, y_value, theta_value))

    if not saw_data:
        errors.append("init_pose.csv has no robots")
    if len(rows) > MAX_ROBOTS:
        errors.append("%d robots exceeds the maximum of %d" % (len(rows), MAX_ROBOTS))

    seen_ids: dict[int, int] = {}
    for line_number, robot_id, _x_value, _y_value, _theta_value in rows:
        if robot_id in seen_ids:
            errors.append(
                "id %d is repeated on rows %d and %d" % (robot_id, seen_ids[robot_id], line_number)
            )
        else:
            seen_ids[robot_id] = line_number

    for index, (line_a, id_a, x_a, y_a, _theta_a) in enumerate(rows):
        for line_b, id_b, x_b, y_b, _theta_b in rows[index + 1 :]:
            distance = math.hypot(x_a - x_b, y_a - y_b)
            if distance < MIN_SEPARATION_M - SEPARATION_TOLERANCE_M:
                errors.append(
                    "robots %d and %d are %.3f m apart; the minimum is %.2f m"
                    % (id_a, id_b, distance, MIN_SEPARATION_M)
                )
    return errors


def _read_regular_file(path: Path, label: str) -> tuple[bytes | None, str | None]:
    if path.is_symlink():
        return None, "%s is a symlink; submit a regular file" % label
    if not path.is_file():
        return None, "%s is missing" % label
    data = path.read_bytes()
    if not data.strip():
        return None, "%s is empty" % label
    if len(data) > MAX_FILE_BYTES:
        return None, "%s is larger than %d bytes" % (label, MAX_FILE_BYTES)
    return data, None


def check_submission_files(folder: Path) -> tuple[list[str], str | None]:
    """Check usr_code.py and init_pose.csv exist as ordinary files.

    email.txt is the legacy notification channel. It is reported and ignored.
    The returned text is the pose file body when both required files loaded.
    """
    errors: list[str] = []
    notes: list[str] = []
    code_path = folder / "usr_code.py"
    pose_path = folder / "init_pose.csv"
    code_bytes, code_error = _read_regular_file(code_path, "usr_code.py")
    pose_bytes, pose_error = _read_regular_file(pose_path, "init_pose.csv")
    if code_error:
        errors.append(code_error)
    if pose_error:
        errors.append(pose_error)
    email_path = folder / "email.txt"
    if email_path.exists():
        notes.append("email.txt is present and ignored; GitHub notifies the pull request author")
    if errors or code_bytes is None or pose_bytes is None:
        return errors + notes, None
    try:
        pose_text = pose_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return errors + ["init_pose.csv is not UTF-8"] + notes, None
    if b"\x00" in code_bytes:
        errors.append("usr_code.py contains a NUL byte")
    try:
        code_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        errors.append("usr_code.py is not UTF-8")
    detail_errors = errors + notes
    if errors:
        return detail_errors, None
    return detail_errors, pose_text


def _python2_command() -> list[str] | None:
    configured = os.environ.get("PY2_BIN", "").strip()
    if configured:
        return [configured]
    for name in ("python2.7", "python2"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def _run_syntax(argv: list[str], timeout: int) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, "syntax check timed out"
    except OSError as err:
        return 1, "could not start the syntax check: %s" % err
    detail = (completed.stdout + completed.stderr).strip()
    return completed.returncode, detail or "syntax check produced no output"


def check_syntax(usr_code: Path, interpreter: str) -> dict[str, object]:
    """Parse usr_code with Python 3 or with a Python 2.7 interpreter.

    Python 2.7 is used as PY2_BIN, python2.7 on PATH, or a python:2.7.18
    container. The container is started with no network. The user file is
    mounted read-only and is not imported.
    """
    script = Path(__file__).resolve().with_name("syntax_check.py")
    if interpreter == "python3":
        code, detail = _run_syntax([sys.executable, str(script), str(usr_code)], timeout=30)
        return {"name": "python3_syntax", "ok": code == 0, "detail": detail}
    if interpreter != "python2":
        raise ValueError("interpreter must be python2 or python3")

    command = _python2_command()
    if command is not None:
        code, detail = _run_syntax(command + [str(script), str(usr_code)], timeout=30)
        return {"name": "python27_syntax", "ok": code == 0, "detail": "Python 2.7: " + detail}
    if shutil.which("docker") is None:
        return {
            "name": "python27_syntax",
            "ok": False,
            "detail": "Python 2.7 was not found. Set PY2_BIN or install Docker so the python:2.7.18-slim image can run.",
        }
    docker_argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        "%s:/syntax_check.py:ro" % script,
        "-v",
        "%s:/usr_code.py:ro" % usr_code.resolve(),
        PYTHON2_IMAGE,
        "python",
        "/syntax_check.py",
        "/usr_code.py",
    ]
    code, detail = _run_syntax(docker_argv, timeout=120)
    return {"name": "python27_syntax", "ok": code == 0, "detail": "Python 2.7 container: " + detail}


def _check(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "ok": ok, "detail": detail}


def validate_folder(
    folder: Path,
    sim_seconds: float,
    simulator_dir: Path | None,
    log_dir: Path | None,
    run_simulator: bool,
) -> dict[str, object]:
    """Run every static check, then the headless simulator when those pass."""
    checks: list[dict[str, object]] = []
    file_notes, pose_text = check_submission_files(folder)
    file_errors = [note for note in file_notes if not note.startswith("email.txt")]
    email_notes = [note for note in file_notes if note.startswith("email.txt")]
    if file_errors:
        file_detail = "; ".join(file_errors)
    else:
        file_detail = "usr_code.py and init_pose.csv are present"
        if email_notes:
            file_detail = file_detail + ". " + "; ".join(email_notes)
    checks.append(_check("files", not file_errors, file_detail))

    pose_ok = False
    if pose_text is None:
        checks.append(_check("init_pose", False, "skipped because the pose file did not load"))
    else:
        pose_errors = validate_init_pose(pose_text)
        pose_ok = not pose_errors
        robot_count = 0
        if pose_ok:
            robot_count = sum(1 for line in pose_text.splitlines() if line.strip())
        detail = (
            "%d robots pass the field, id, and spacing rules" % robot_count
            if pose_ok
            else "; ".join(pose_errors)
        )
        checks.append(_check("init_pose", pose_ok, detail))

    usr_code = folder / "usr_code.py"
    if file_errors:
        checks.append(_check("python3_syntax", False, "skipped because usr_code.py did not load"))
        checks.append(_check("python27_syntax", False, "skipped because usr_code.py did not load"))
        syntax_ok = False
    else:
        py3 = check_syntax(usr_code, "python3")
        py2 = check_syntax(usr_code, "python2")
        checks.append(py3)
        checks.append(py2)
        syntax_ok = bool(py3["ok"] and py2["ok"])

    if run_simulator and pose_ok and syntax_ok and pose_text is not None:
        sim_log = None if log_dir is None else log_dir / folder.name
        checks.append(
            run_headless_simulation(
                usr_code=usr_code,
                init_pose_text=pose_text,
                sim_seconds=sim_seconds,
                simulator_dir=simulator_dir,
                log_dir=sim_log,
            )
        )
    else:
        reason = "skipped because an earlier check failed"
        if not run_simulator:
            reason = "skipped by request"
        skipped_ok = reason == "skipped by request"
        checks.append(_check("simulator", skipped_ok, reason))

    failed = [check for check in checks if not check["ok"]]
    return {
        "path": "submissions/" + folder.name,
        "ok": not failed,
        "checks": checks,
    }


def _git_changed_paths(root: Path, base_sha: str, head_sha: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", base_sha, head_sha],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git diff failed").strip()
        raise RuntimeError(detail)
    return [line for line in completed.stdout.splitlines() if line]


def _all_submission_dirs(root: Path) -> list[str]:
    base = root / "submissions"
    if not base.is_dir():
        return []
    names = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and SUBMISSION_NAME_RE.fullmatch(child.name):
            names.append("submissions/" + child.name)
    return names


def select_submissions(root: Path, explicit: str, base_sha: str, head_sha: str) -> tuple[list[str], str]:
    """Choose which submission folders to check.

    An explicit directory wins. A pull-request diff checks only the folders
    it touches. No diff and no explicit path checks every folder, which is
    the manual workflow_dispatch behavior. Returns a note when a pull
    request did not touch a submission.
    """
    if explicit.strip():
        folder = explicit.strip().strip("/")
        parts = folder.split("/")
        if len(parts) != 2 or parts[0] != "submissions" or not SUBMISSION_NAME_RE.fullmatch(parts[1]):
            raise ValueError(
                "submission directory must look like submissions/<name>, got %r" % explicit.strip()
            )
        return [folder], ""
    if base_sha.strip() and head_sha.strip():
        changed = submission_dirs_in(_git_changed_paths(root, base_sha.strip(), head_sha.strip()))
        if not changed:
            return [], NO_CHANGES_NOTE
        return changed, ""
    return _all_submission_dirs(root), ""


def _parse_sim_seconds(raw: str) -> float:
    text = raw.strip() or str(DEFAULT_SIM_SECONDS)
    try:
        value = float(text)
    except ValueError as err:
        raise ValueError("sim seconds must be a number") from err
    if value < 1 or value > MAX_SIM_SECONDS:
        raise ValueError("sim seconds must be between 1 and %d" % MAX_SIM_SECONDS)
    return value


def build_summary(root: Path) -> dict[str, object]:
    """Validate the submissions selected by arguments and environment variables."""
    try:
        return _build_summary(root)
    except ValueError as err:
        return {
            "version": SUMMARY_VERSION,
            "ok": False,
            "result": "error",
            "note": str(err),
            "pr_number": None,
            "head_sha": "",
            "submissions": [],
        }


def _build_summary(root: Path) -> dict[str, object]:
    explicit = os.environ.get("SUBMISSION_DIR", "")
    base_sha = os.environ.get("BASE_SHA", "")
    head_sha = os.environ.get("PR_HEAD_SHA", "")
    sim_seconds = _parse_sim_seconds(os.environ.get("SIM_SECONDS", ""))
    run_simulator = os.environ.get("SKIP_SIM", "").strip() not in {"1", "true", "yes"}
    simulator_value = os.environ.get("SIMULATOR_DIR", "").strip()
    simulator_dir = Path(simulator_value) if simulator_value else root / "third_party" / "swarm-simulator"
    log_dir = Path(os.environ.get("VALIDATION_LOG_DIR", root / "validation-logs"))
    pr_number = os.environ.get("PR_NUMBER", "").strip()
    recorded_sha = head_sha.strip() or os.environ.get("COMMIT_SHA", "").strip()

    try:
        folders, note = select_submissions(root, explicit, base_sha, head_sha)
    except (RuntimeError, ValueError) as err:
        return {
            "version": SUMMARY_VERSION,
            "ok": False,
            "result": "error",
            "note": str(err),
            "pr_number": pr_number if pr_number.isdigit() else None,
            "head_sha": recorded_sha,
            "submissions": [],
        }

    if len(folders) > MAX_SUBMISSIONS:
        return {
            "version": SUMMARY_VERSION,
            "ok": False,
            "result": "error",
            "note": "%d submissions exceeds the limit of %d per run" % (len(folders), MAX_SUBMISSIONS),
            "pr_number": pr_number if pr_number.isdigit() else None,
            "head_sha": recorded_sha,
            "submissions": [],
        }

    if not folders:
        return {
            "version": SUMMARY_VERSION,
            "ok": True,
            "result": "no_changes" if note == NO_CHANGES_NOTE else "checked",
            "note": note or "There are no submissions to check.",
            "pr_number": pr_number if pr_number.isdigit() else None,
            "head_sha": recorded_sha,
            "submissions": [],
        }

    results = []
    for folder_name in folders:
        folder = root / folder_name
        if not folder.is_dir():
            results.append(
                {
                    "path": folder_name,
                    "ok": False,
                    "checks": [_check("files", False, "%s does not exist" % folder_name)],
                }
            )
            continue
        results.append(
            validate_folder(
                folder=folder,
                sim_seconds=sim_seconds,
                simulator_dir=simulator_dir,
                log_dir=log_dir,
                run_simulator=run_simulator,
            )
        )
    return {
        "version": SUMMARY_VERSION,
        "ok": all(bool(item["ok"]) for item in results),
        "result": "checked",
        "note": "",
        "pr_number": pr_number if pr_number.isdigit() else None,
        "head_sha": recorded_sha,
        "submissions": results,
    }


def format_summary(summary: dict[str, object]) -> str:
    """Plain-text rendering of a summary for the Actions log."""
    lines = ["result: %s" % ("passed" if summary.get("ok") else "failed")]
    note = summary.get("note")
    if isinstance(note, str) and note:
        lines.append(note)
    submissions = summary.get("submissions")
    if isinstance(submissions, list):
        for item in submissions:
            if not isinstance(item, dict):
                continue
            lines.append("%s: %s" % (item.get("path"), "passed" if item.get("ok") else "failed"))
            checks = item.get("checks")
            if not isinstance(checks, list):
                continue
            for check in checks:
                if not isinstance(check, dict):
                    continue
                lines.append(
                    "  - %s: %s (%s)"
                    % (check.get("name"), "ok" if check.get("ok") else "FAILED", check.get("detail"))
                )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Coachbot submission folders")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        summary = build_summary(root)
    except ValueError as err:
        print(str(err), file=sys.stderr)
        return 2
    text = format_summary(summary)
    sys.stdout.write(text)
    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
