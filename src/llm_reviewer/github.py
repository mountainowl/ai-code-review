"""GitHub REST client used by the GitHub provider.

Stdlib-only, mirroring :mod:`llm_reviewer.gitlab` in spirit but speaking
GitHub's REST dialect:

* Auth is ``Authorization: Bearer <token>`` with the
  ``application/vnd.github+json`` Accept header and a pinned API version.
* Pagination follows the ``Link: <url>; rel="next"`` header (GitHub does
  not expose ``X-Next-Page``).
* Rate limiting surfaces as ``429`` (secondary) or ``403`` with
  ``X-RateLimit-Remaining: 0`` (primary); both are retried with
  ``Retry-After`` honored when present.

A project is a GitHub ``owner/repo`` slug. As with the GitLab client, this
module does NOT post inline comments through REST in the normal path — that
goes through the GitHub MCP server (see
:mod:`llm_reviewer.scm.github`). :func:`create_pr_review_comment` is the
REST fallback used only when MCP returns no comment ID.

The ``main`` entry point (``gh-review-poller``) runs the shared poller with
the provider forced to GitHub.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast

from llm_reviewer.review_config import ReviewConfig
from llm_reviewer.types import JsonObject

API_MAX_ATTEMPTS = 3
API_RETRY_STATUSES = {429, 500, 502, 503, 504}
API_VERSION = "2022-11-28"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "Content-Type": "application/json",
    }


def _request(
    url: str, token: str, method: str, body: JsonObject | None = None
) -> tuple[Any, dict[str, str]]:
    """Issue one GitHub REST request to an absolute URL, with retry.

    Retries on transient statuses and connection errors, honoring
    ``Retry-After``. A ``403`` with ``X-RateLimit-Remaining: 0`` is treated
    as a (retryable) rate-limit rather than a hard auth failure.
    """
    payload = None if body is None else json.dumps(body).encode()
    for attempt in range(API_MAX_ATTEMPTS):
        req = urllib.request.Request(url, data=payload, method=method, headers=_headers(token))
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read().decode() or "null"
                headers = dict(resp.headers)
            return json.loads(data), headers
        except urllib.error.HTTPError as exc:
            retryable = exc.code in API_RETRY_STATUSES or _is_rate_limited(exc)
            if not retryable or attempt == API_MAX_ATTEMPTS - 1:
                raise
            time.sleep(api_retry_delay(exc.headers, attempt))
        except urllib.error.URLError:
            if attempt == API_MAX_ATTEMPTS - 1:
                raise
            time.sleep(api_retry_delay({}, attempt))
    raise RuntimeError("GitHub API retry loop exhausted")


def _is_rate_limited(exc: urllib.error.HTTPError) -> bool:
    if exc.code != 403:
        return False
    remaining = exc.headers.get("X-RateLimit-Remaining") if hasattr(exc.headers, "get") else None
    return remaining == "0"


def api_retry_delay(headers: object, attempt: int) -> float:
    retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
    if retry_after:
        try:
            return min(60.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    delay = 0.5 * (2**attempt)
    return 10.0 if delay > 10.0 else delay


def api(
    api_url: str, token: str, method: str, path: str, body: JsonObject | None = None
) -> tuple[Any, dict[str, str]]:
    """Issue one GitHub REST call against ``api_url`` + ``path``."""
    return _request(api_url.rstrip("/") + path, token, method, body)


def _next_link(headers: dict[str, str]) -> str | None:
    """Return the ``rel="next"`` URL from a GitHub ``Link`` header, if any."""
    link = headers.get("Link") or headers.get("link")
    if not link:
        return None
    for part in link.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url = segments[0].strip().strip("<>")
        if any(seg.strip() == 'rel="next"' for seg in segments[1:]):
            return url
    return None


def api_pages(api_url: str, token: str, path: str) -> list[JsonObject]:
    """Fetch all pages of a GitHub list endpoint, following ``Link`` next."""
    sep = "&" if "?" in path else "?"
    url: str | None = api_url.rstrip("/") + f"{path}{sep}per_page=100"
    out: list[JsonObject] = []
    while url:
        data, headers = _request(url, token, "GET")
        if not isinstance(data, list):
            raise RuntimeError("GitHub API page did not return a list")
        out.extend(cast(JsonObject, item) for item in data if isinstance(item, dict))
        url = _next_link(headers)
    return out


def _owner_repo(project: str) -> str:
    """Return the URL-safe ``owner/repo`` path segment for a project slug."""
    owner, _, repo = project.partition("/")
    return f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo, safe='')}"


def open_prs(cfg: ReviewConfig, project: str, token: str) -> list[JsonObject]:
    """Return open pull requests for a GitHub repository."""
    repo = _owner_repo(project)
    return api_pages(cfg.github_api_url, token, f"/repos/{repo}/pulls?state=open")


def get_pr(cfg: ReviewConfig, token: str, project: str, number: int) -> JsonObject:
    """Return a single pull-request payload."""
    repo = _owner_repo(project)
    data, _ = api(cfg.github_api_url, token, "GET", f"/repos/{repo}/pulls/{number}")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub PR response was not an object")
    return cast(JsonObject, data)


def get_pr_files(cfg: ReviewConfig, token: str, project: str, number: int) -> list[JsonObject]:
    """Return per-file diff entries for a pull request (``filename``/``patch``)."""
    repo = _owner_repo(project)
    return api_pages(cfg.github_api_url, token, f"/repos/{repo}/pulls/{number}/files")


def get_pr_review_comments(
    cfg: ReviewConfig, token: str, project: str, number: int
) -> list[JsonObject]:
    """Return all inline review comments on a pull request."""
    repo = _owner_repo(project)
    return api_pages(cfg.github_api_url, token, f"/repos/{repo}/pulls/{number}/comments")


def get_pr_review_comment(
    cfg: ReviewConfig, token: str, project: str, comment_id: str
) -> JsonObject:
    """Return one inline review comment by ID."""
    repo = _owner_repo(project)
    encoded = urllib.parse.quote(comment_id, safe="")
    data, _ = api(cfg.github_api_url, token, "GET", f"/repos/{repo}/pulls/comments/{encoded}")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub review-comment response was not an object")
    return cast(JsonObject, data)


def find_review_comment_by_body(
    cfg: ReviewConfig, token: str, project: str, number: int, body: str
) -> str:
    """Locate an existing review comment by exact body match; return its ID or ""."""
    for comment in get_pr_review_comments(cfg, token, project, number):
        if comment.get("body") == body and comment.get("id"):
            return str(comment["id"])
    return ""


def create_pr_review_comment(
    cfg: ReviewConfig,
    token: str,
    project: str,
    number: int,
    body: str,
    position: JsonObject,
) -> JsonObject:
    """Create an inline review comment via REST (fallback for the MCP path).

    ``position`` carries GitHub's comment anchor: ``commit_id``, ``path``,
    ``line``, and ``side`` (plus optional ``start_line``/``start_side`` for
    multi-line). See :mod:`llm_reviewer.scm.github` for how it's built.
    """
    repo = _owner_repo(project)
    payload = {"body": body, **position}
    data, _ = api(
        cfg.github_api_url, token, "POST", f"/repos/{repo}/pulls/{number}/comments", payload
    )
    return cast(JsonObject, data) if isinstance(data, dict) else {}


def classify_review_thread_outcome(
    comment: JsonObject, replies: list[JsonObject], bot_username: str, pr_state: str
) -> JsonObject:
    """Classify a posted review comment + its replies for outcome sync.

    GitHub exposes review-thread *resolution* state only through GraphQL,
    not REST. This REST-based classifier therefore reports everything it
    can see — developer replies, manual markers, deletion, merged-state —
    and leaves ``resolved`` as ``False`` (a known REST limitation,
    documented in the README). ``merged_unresolved`` is true when the PR
    merged without the thread being resolved.
    """
    active_replies = [r for r in replies if not r.get("deleted")]
    developer_replied = any(
        ((r.get("user") or {}).get("login") or "") != bot_username for r in active_replies
    )
    reply_text = "\n".join(str(r.get("body") or "").lower() for r in active_replies)
    false_positive = "[llm-review:false-positive]" in reply_text
    duplicate = "[llm-review:duplicate]" in reply_text
    disputed = "[llm-review:disputed]" in reply_text or false_positive
    deleted = bool(comment.get("deleted", False))
    return {
        # Resolution state is GraphQL-only; REST cannot observe it.
        "resolved": False,
        "deleted": deleted,
        "developer_replied": developer_replied,
        "disputed": disputed,
        "false_positive": false_positive,
        "duplicate": duplicate,
        "resolved_at": None,
        "merged_unresolved": pr_state == "merged",
    }


def main() -> int:
    """CLI entry point for ``gh-review-poller``.

    Runs the shared poller with the provider forced to GitHub via the
    ``LLM_REVIEWER_PROVIDER`` override, regardless of ``[scm].provider`` in
    config. This lets a single host poll both GitLab (``mr-review-poller``)
    and GitHub (``gh-review-poller``) from one install.
    """
    os.environ["LLM_REVIEWER_PROVIDER"] = "github"
    from llm_reviewer.poller import main as poller_main

    return poller_main()


if __name__ == "__main__":
    raise SystemExit(main())
