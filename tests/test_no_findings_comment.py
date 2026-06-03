"""Tests for the "no issues found" change-level comment.

Covers the three layers added for issue #13:

* Config parsing — defaults and overrides for ``post_no_findings_comment``
  and ``no_findings_comment_body`` in ``[agents]``.
* REST helpers — ``find_note_by_body`` / ``create_mr_note`` on GitLab and
  ``find_issue_comment_by_body`` / ``create_issue_comment`` on GitHub.
* Provider integration — ``GitLabProvider.post_change_comment`` and
  ``GitHubProvider.post_change_comment`` dedup by exact body match.
* Poller helper — ``post_no_findings_comment`` honors the flag, the
  dry-run gate, and an empty message.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from llm_reviewer.poller import post_no_findings_comment
from llm_reviewer.review_config import (
    DEFAULT_NO_FINDINGS_COMMENT,
    ReviewConfig,
    review_config_from_dict,
)

# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_review_config_defaults_no_findings_comment_to_on() -> None:
    cfg = review_config_from_dict({})

    assert cfg.post_no_findings_comment is True
    assert cfg.no_findings_comment_body == DEFAULT_NO_FINDINGS_COMMENT
    # Sanity: the default body actually communicates the signal.
    assert "no issues" in DEFAULT_NO_FINDINGS_COMMENT.lower()


def test_review_config_parses_custom_no_findings_comment_block() -> None:
    cfg = review_config_from_dict(
        {
            "agents": {
                "post_no_findings_comment": False,
                "no_findings_comment_body": "All good ✅",
            }
        }
    )

    assert cfg.post_no_findings_comment is False
    assert cfg.no_findings_comment_body == "All good ✅"


# ---------------------------------------------------------------------------
# Poller helper — verdict matrix
# ---------------------------------------------------------------------------


def test_post_no_findings_comment_disabled_when_flag_off() -> None:
    cfg = ReviewConfig(post_no_findings_comment=False, dry_run=False)
    provider = MagicMock()

    verdict = post_no_findings_comment(
        cfg=cfg, token="tok", project="grp/repo", number=1, provider=provider
    )

    assert verdict == "disabled"
    provider.post_change_comment.assert_not_called()


def test_post_no_findings_comment_disabled_when_body_is_blank() -> None:
    cfg = ReviewConfig(
        post_no_findings_comment=True,
        no_findings_comment_body="   ",
        dry_run=False,
    )
    provider = MagicMock()

    verdict = post_no_findings_comment(
        cfg=cfg, token="tok", project="grp/repo", number=1, provider=provider
    )

    assert verdict == "disabled"
    provider.post_change_comment.assert_not_called()


def test_post_no_findings_comment_skipped_in_dry_run() -> None:
    cfg = ReviewConfig(post_no_findings_comment=True, dry_run=True)
    provider = MagicMock()

    verdict = post_no_findings_comment(
        cfg=cfg, token="tok", project="grp/repo", number=1, provider=provider
    )

    assert verdict == "skipped_dry_run"
    provider.post_change_comment.assert_not_called()


def test_post_no_findings_comment_posts_when_enabled_and_not_dry_run() -> None:
    cfg = ReviewConfig(
        post_no_findings_comment=True,
        no_findings_comment_body="Reviewer pass ✅",
        dry_run=False,
    )
    provider = MagicMock()
    provider.post_change_comment.return_value = "note-7"

    verdict = post_no_findings_comment(
        cfg=cfg, token="tok", project="grp/repo", number=1, provider=provider
    )

    assert verdict == "posted"
    provider.post_change_comment.assert_called_once_with(
        cfg, "tok", "grp/repo", 1, "Reviewer pass ✅"
    )


# ---------------------------------------------------------------------------
# GitLab REST helpers
# ---------------------------------------------------------------------------


def test_gitlab_find_note_by_body_matches_exact_body() -> None:
    from llm_reviewer import gitlab

    notes = [
        {"id": 1, "system": True, "body": "matched marker"},  # ignored: system note
        {"id": 2, "body": "other body"},
        {"id": 3, "body": "matched marker"},
    ]
    with patch("llm_reviewer.gitlab.get_mr_notes", return_value=notes):
        note_id = gitlab.find_note_by_body(ReviewConfig(), "tok", "grp/repo", 1, "matched marker")

    assert note_id == "3"


def test_gitlab_find_note_by_body_returns_empty_when_no_match() -> None:
    from llm_reviewer import gitlab

    with patch("llm_reviewer.gitlab.get_mr_notes", return_value=[{"id": 1, "body": "nope"}]):
        note_id = gitlab.find_note_by_body(ReviewConfig(), "tok", "grp/repo", 1, "missing")

    assert note_id == ""


# ---------------------------------------------------------------------------
# GitLab provider — dedup vs. create
# ---------------------------------------------------------------------------


def test_gitlab_provider_post_change_comment_reuses_existing_note() -> None:
    from llm_reviewer.scm.gitlab import GitLabProvider

    with patch("llm_reviewer.gitlab.find_note_by_body", return_value="note-existing") as finder:
        with patch("llm_reviewer.gitlab.create_mr_note") as creator:
            note_id = GitLabProvider().post_change_comment(
                ReviewConfig(), "tok", "grp/repo", 1, "body"
            )

    assert note_id == "note-existing"
    finder.assert_called_once()
    creator.assert_not_called()


def test_gitlab_provider_post_change_comment_creates_when_missing() -> None:
    from llm_reviewer.scm.gitlab import GitLabProvider

    with patch("llm_reviewer.gitlab.find_note_by_body", return_value=""):
        with patch("llm_reviewer.gitlab.create_mr_note", return_value={"id": 42}) as creator:
            note_id = GitLabProvider().post_change_comment(
                ReviewConfig(), "tok", "grp/repo", 1, "body"
            )

    assert note_id == "42"
    creator.assert_called_once_with(ReviewConfig(), "tok", "grp/repo", 1, "body")


def test_gitlab_provider_post_change_comment_returns_blank_when_create_lacks_id() -> None:
    from llm_reviewer.scm.gitlab import GitLabProvider

    with patch("llm_reviewer.gitlab.find_note_by_body", return_value=""):
        with patch("llm_reviewer.gitlab.create_mr_note", return_value={}):
            note_id = GitLabProvider().post_change_comment(
                ReviewConfig(), "tok", "grp/repo", 1, "body"
            )

    assert note_id == ""


# ---------------------------------------------------------------------------
# GitHub REST helpers + provider
# ---------------------------------------------------------------------------


def test_github_find_issue_comment_by_body_matches_exact_body() -> None:
    from llm_reviewer import github

    comments = [
        {"id": 11, "body": "different"},
        {"id": 22, "body": "match"},
    ]
    with patch("llm_reviewer.github.get_issue_comments", return_value=comments):
        comment_id = github.find_issue_comment_by_body(
            ReviewConfig(provider="github"), "tok", "owner/repo", 5, "match"
        )

    assert comment_id == "22"


def test_github_provider_post_change_comment_reuses_existing_comment() -> None:
    from llm_reviewer.scm.github import GitHubProvider

    with patch(
        "llm_reviewer.github.find_issue_comment_by_body", return_value="comment-existing"
    ) as finder:
        with patch("llm_reviewer.github.create_issue_comment") as creator:
            comment_id = GitHubProvider().post_change_comment(
                ReviewConfig(provider="github"), "tok", "owner/repo", 5, "body"
            )

    assert comment_id == "comment-existing"
    finder.assert_called_once()
    creator.assert_not_called()


def test_github_provider_post_change_comment_creates_when_missing() -> None:
    from llm_reviewer.scm.github import GitHubProvider

    with patch("llm_reviewer.github.find_issue_comment_by_body", return_value=""):
        with patch("llm_reviewer.github.create_issue_comment", return_value={"id": 99}) as creator:
            comment_id = GitHubProvider().post_change_comment(
                ReviewConfig(provider="github"), "tok", "owner/repo", 5, "body"
            )

    assert comment_id == "99"
    creator.assert_called_once_with(ReviewConfig(provider="github"), "tok", "owner/repo", 5, "body")


# ---------------------------------------------------------------------------
# env.example.toml documents the new keys
# ---------------------------------------------------------------------------


def test_env_example_documents_no_findings_comment_keys() -> None:
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / "config" / "env.example.toml").read_text())
    agents = cfg.get("agents", {})

    assert agents.get("post_no_findings_comment") is True
    assert agents.get("no_findings_comment_body") == DEFAULT_NO_FINDINGS_COMMENT
