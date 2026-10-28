"""Small GitHub REST helpers used to comment on pull requests.

Issue comments are the pull-request conversation. Review comments are a
different API and are not used here.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable

API_ROOT = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RequestFn = Callable[..., tuple[int, object, dict[str, str]]]


def github_request(
    method: str,
    url: str,
    token: str,
    payload: object | None = None,
    timeout: int = 30,
) -> tuple[int, object, dict[str, str]]:
    """Send one GitHub API request.

    Returns the status code, a decoded JSON value when the body is JSON,
    otherwise the raw text, and the response headers. HTTP error statuses
    are returned rather than raised.
    """
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "coachbot-submission-workflow")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            headers = {key.lower(): value for key, value in response.headers.items()}
            status = response.status
    except urllib.error.HTTPError as err:
        raw = err.read()
        headers = {key.lower(): value for key, value in err.headers.items()}
        status = err.code
    text = raw.decode("utf-8", "replace")
    if not text:
        return status, None, headers
    try:
        return status, json.loads(text), headers
    except json.JSONDecodeError:
        return status, text, headers


def find_open_pull_request(
    repo: str,
    sha: str,
    token: str,
    request: RequestFn = github_request,
) -> int | None:
    """Return the open pull request whose head commit is sha, if there is one.

    The lookup uses the base repository's open pull requests, so a forked
    pull request is found from the commit GitHub already associated with it.
    The artifact uploaded by that pull request is not consulted.
    """
    if not REPO_RE.fullmatch(repo):
        raise ValueError("repository must look like owner/name")
    if not SHA_RE.fullmatch(sha):
        raise ValueError("head sha must be 40 hex characters")
    for page in range(1, 4):
        status, body, _headers = request(
            "GET",
            "%s/repos/%s/pulls?state=open&per_page=100&page=%d" % (API_ROOT, repo, page),
            token,
        )
        if status != 200 or not isinstance(body, list):
            raise RuntimeError("listing pull requests failed with HTTP %s" % status)
        for pull in body:
            if not isinstance(pull, dict):
                continue
            head = pull.get("head")
            number = pull.get("number")
            if isinstance(head, dict) and head.get("sha") == sha and isinstance(number, int):
                return number
        if len(body) < 100:
            return None
    return None


def list_pull_request_paths(
    repo: str,
    number: int,
    token: str,
    request: RequestFn = github_request,
) -> list[str]:
    """Return file paths changed by one pull request.

    A page of 100 files is the limit. More than that is refused so a huge
    pull request cannot be matched to the wrong submission by a truncated list.
    """
    if not REPO_RE.fullmatch(repo):
        raise ValueError("repository must look like owner/name")
    if number < 1:
        raise ValueError("pull request number must be positive")
    status, body, _headers = request(
        "GET",
        "%s/repos/%s/pulls/%d/files?per_page=100" % (API_ROOT, repo, number),
        token,
    )
    if status != 200 or not isinstance(body, list):
        raise RuntimeError("listing pull request files failed with HTTP %s" % status)
    if len(body) >= 100:
        raise RuntimeError("pull request changes at least 100 files; name the submission explicitly")
    paths: list[str] = []
    for item in body:
        if isinstance(item, dict) and isinstance(item.get("filename"), str):
            paths.append(item["filename"])
    return paths


def upsert_issue_comment(
    repo: str,
    number: int,
    marker: str,
    body: str,
    token: str,
    request: RequestFn = github_request,
) -> str:
    """Create or replace the pull-request comment that contains marker.

    Returns "created", "updated", or "forbidden". Forbidden is not an
    exception: fork pull requests often receive a read-only token, and the
    trusted workflow_run job comments instead.
    """
    if not REPO_RE.fullmatch(repo):
        raise ValueError("repository must look like owner/name")
    if number < 1:
        raise ValueError("pull request number must be positive")
    if marker not in body:
        raise ValueError("comment body is missing its marker")
    list_url = "%s/repos/%s/issues/%d/comments?per_page=100" % (API_ROOT, repo, number)
    status, existing, _headers = request("GET", list_url, token)
    if status == 403:
        return "forbidden"
    if status != 200 or not isinstance(existing, list):
        raise RuntimeError("listing comments failed with HTTP %s" % status)
    for comment in existing:
        if not isinstance(comment, dict):
            continue
        if marker not in str(comment.get("body") or ""):
            continue
        comment_url = comment.get("url")
        if not isinstance(comment_url, str) or not comment_url.startswith(API_ROOT + "/"):
            continue
        update_status, _update_body, _update_headers = request(
            "PATCH", comment_url, token, {"body": body}
        )
        if update_status == 403:
            return "forbidden"
        if update_status not in (200, 201):
            raise RuntimeError("updating the comment failed with HTTP %s" % update_status)
        return "updated"
    create_status, _create_body, _create_headers = request(
        "POST",
        "%s/repos/%s/issues/%d/comments" % (API_ROOT, repo, number),
        token,
        {"body": body},
    )
    if create_status == 403:
        return "forbidden"
    if create_status not in (200, 201):
        raise RuntimeError("creating the comment failed with HTTP %s" % create_status)
    return "created"
