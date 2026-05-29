"""Unit tests for the GitHub REST client and GitHub provider."""

from __future__ import annotations

from unittest.mock import patch

from llm_reviewer import github
from llm_reviewer.review_config import ReviewConfig
from llm_reviewer.scm import get_provider
from llm_reviewer.scm.github import GitHubProvider


def test_get_provider_returns_github_for_github_config() -> None:
    provider = get_provider(ReviewConfig(provider="github"))
    assert provider.name == "github"
    assert isinstance(provider, GitHubProvider)


def test_next_link_parses_rel_next() -> None:
    headers = {
        "Link": '<https://api.github.com/x?page=2>; rel="next", '
        '<https://api.github.com/x?page=9>; rel="last"'
    }
    assert github._next_link(headers) == "https://api.github.com/x?page=2"


def test_next_link_none_when_absent() -> None:
    assert github._next_link({}) is None
    assert github._next_link({"Link": '<https://x>; rel="last"'}) is None


def test_api_pages_follows_link_header() -> None:
    pages = {
        "https://api.github.com/repos/o/r/pulls?state=open&per_page=100": (
            [{"number": 1}],
            {"Link": '<https://api.github.com/p2>; rel="next"'},
        ),
        "https://api.github.com/p2": ([{"number": 2}], {}),
    }

    def fake_request(url, token, method, body=None):
        return pages[url]

    with patch("llm_reviewer.github._request", side_effect=fake_request):
        cfg = ReviewConfig(provider="github")
        prs = github.open_prs(cfg, "o/r", "token")

    assert [pr["number"] for pr in prs] == [1, 2]


def test_provider_change_number_and_head_sha() -> None:
    provider = GitHubProvider()
    change = {"number": 42, "head": {"sha": "deadbeef"}}
    assert provider.change_number(change) == 42
    assert provider.head_sha(change) == "deadbeef"
    assert provider.head_sha({"number": 1}) == ""


def test_provider_changed_lines_from_github_files() -> None:
    provider = GitHubProvider()
    files = [{"filename": "src/A.py", "patch": "@@ -1,1 +1,2 @@\n old\n+new\n"}]
    with patch("llm_reviewer.github.get_pr_files", return_value=files):
        changed = provider.changed_lines(ReviewConfig(provider="github"), "tok", "o/r", 1)
    assert 2 in changed["src/A.py"]["new_lines"]
    assert 1 not in changed["src/A.py"]["new_lines"]


def test_provider_build_position_uses_line_and_side() -> None:
    provider = GitHubProvider()
    change = {"head": {"sha": "abc123"}}
    changed = {"src/A.py": {"new_path": "src/A.py", "old_path": "src/A.py", "new_lines": {7}}}

    position = provider.build_position(change, changed, {"file": "src/A.py", "line": 7})
    assert position == {"commit_id": "abc123", "path": "src/A.py", "line": 7, "side": "RIGHT"}

    # Line not in the diff → not placeable.
    assert provider.build_position(change, changed, {"file": "src/A.py", "line": 99}) is None
    # No head sha → not placeable.
    assert provider.build_position({}, changed, {"file": "src/A.py", "line": 7}) is None


def test_provider_post_falls_back_to_rest_when_mcp_fails() -> None:
    provider = GitHubProvider()
    position = {"commit_id": "abc", "path": "src/A.py", "line": 7, "side": "RIGHT"}

    with patch("llm_reviewer.mcp.call_tool", side_effect=RuntimeError("no such tool")):
        with patch("llm_reviewer.github.find_review_comment_by_body", return_value=""):
            with patch(
                "llm_reviewer.github.create_pr_review_comment",
                return_value={"id": "gh-comment-1"},
            ):
                comment_id = provider.post_inline_comment(
                    ReviewConfig(provider="github"), "tok", "o/r", 5, "body", position
                )

    assert comment_id == "gh-comment-1"


def test_provider_post_prefers_mcp_when_it_returns_id() -> None:
    provider = GitHubProvider()
    position = {"commit_id": "abc", "path": "src/A.py", "line": 7, "side": "RIGHT"}

    with patch("llm_reviewer.mcp.call_tool", return_value={"id": "mcp-comment-9"}):
        comment_id = provider.post_inline_comment(
            ReviewConfig(provider="github"), "tok", "o/r", 5, "body", position
        )

    assert comment_id == "mcp-comment-9"


def test_classify_review_thread_outcome_reads_markers_and_replies() -> None:
    comment = {"id": "c1", "body": "finding"}
    replies = [
        {"user": {"login": "dev1"}, "body": "[llm-review:false-positive] nope"},
    ]
    outcome = github.classify_review_thread_outcome(
        comment, replies, bot_username="llm-reviewer", pr_state="merged"
    )
    assert outcome["developer_replied"] is True
    assert outcome["false_positive"] is True
    assert outcome["disputed"] is True
    # Resolution is GraphQL-only; REST classifier reports unresolved.
    assert outcome["resolved"] is False
    assert outcome["merged_unresolved"] is True


def test_provider_review_prompt_mentions_github_pr() -> None:
    provider = GitHubProvider()
    change = {
        "html_url": "https://github.com/o/r/pull/5",
        "number": 5,
        "title": "Fix bug",
        "head": {"ref": "feature", "sha": "abc"},
        "base": {"ref": "main"},
    }
    prompt = provider.review_prompt("o/r", change, ReviewConfig(provider="github"))
    assert "GitHub PR" in prompt
    assert "PR number: 5" in prompt
    assert "Use the `code-reviewer` skill" in prompt
