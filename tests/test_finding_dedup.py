"""Cross-commit finding dedup — regression tests for the duplicate-thread bug.

Before the fix, a re-review at a new SHA re-posted every still-present finding
as a new thread, because both dedup layers keyed on values that change run to
run: the DB fingerprint mixes in the commit SHA *and* the LLM-written body, and
the provider's body matcher is verbatim. The fix adds a SHA- and
wording-independent ``dedup_key`` plus a cross-SHA ``finding_posted_on_mr``
check before posting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bubo import db, paths, poller
from bubo.findings import finding_dedup_key
from bubo.review_config import ReviewConfig
from bubo.statuses import FindingStatus

# ---------------------------------------------------------------------------
# finding_dedup_key — the stable identity
# ---------------------------------------------------------------------------


def test_dedup_key_ignores_sha_and_wording() -> None:
    # The exact scenario from the bug: same locus + taxonomy, different prose.
    a = finding_dedup_key(
        "group/repo",
        1,
        {
            "file": "gradle.properties",
            "line": 3,
            "category": "security",
            "severity": "blocking",
            "type": "issue",
            "title": "private token is committed",
            "body": "Impact: anyone can use it",
        },
    )
    b = finding_dedup_key(
        "group/repo",
        1,
        {
            "file": "gradle.properties",
            "line": 3,
            "category": "security",
            "severity": "blocking",
            "type": "issue",
            "title": "GitLab token is committed",
            "body": "Impact: anyone with access can use it",
        },
    )
    assert a == b


def test_dedup_key_distinguishes_locus_and_taxonomy() -> None:
    base = {
        "file": "gradle.properties",
        "line": 3,
        "category": "security",
        "severity": "blocking",
        "type": "issue",
    }
    key = finding_dedup_key("group/repo", 1, base)
    assert key != finding_dedup_key("group/repo", 1, {**base, "line": 4})
    assert key != finding_dedup_key("group/repo", 1, {**base, "file": "build.gradle.kts"})
    assert key != finding_dedup_key("group/repo", 1, {**base, "category": "style"})
    # Different MR is a different context.
    assert key != finding_dedup_key("group/repo", 2, base)


def test_dedup_key_treats_string_and_int_line_as_equal() -> None:
    # The LLM may emit line as a JSON number on one run and a string on the
    # next; for an unchanged line that must not produce a fresh key.
    base = {"file": "f", "category": "security", "severity": "blocking", "type": "issue"}
    assert finding_dedup_key("group/repo", 1, {**base, "line": 12}) == finding_dedup_key(
        "group/repo", 1, {**base, "line": "12"}
    )


def test_dedup_key_defaults_missing_taxonomy_like_finding_body() -> None:
    # A finding that omits type/severity/category must key the same as one that
    # spells out finding_body's defaults — never degrade to file+line alone.
    explicit = finding_dedup_key(
        "group/repo",
        1,
        {"file": "f", "line": 3, "type": "issue", "severity": "blocking", "category": "correctness"},
    )
    bare = finding_dedup_key("group/repo", 1, {"file": "f", "line": 3})
    assert explicit == bare


# ---------------------------------------------------------------------------
# finding_posted_on_mr — the cross-SHA reader
# ---------------------------------------------------------------------------


def _finding(line: int = 3) -> dict[str, object]:
    return {
        "type": "issue",
        "severity": "blocking",
        "category": "security",
        "file": "gradle.properties",
        "line": line,
        "title": "token is committed",
    }


def test_finding_posted_on_mr_matches_across_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "DB", tmp_path / "reviewer.sqlite")
    db.init_db()
    key = finding_dedup_key("group/repo", 1, _finding())
    db.record_finding(
        project="group/repo",
        iid=1,
        sha="sha-A",
        fingerprint="fp-A",
        finding=_finding(),
        status=FindingStatus.POSTED,
        body="body",
        dedup_key=key,
    )

    # Matches the same key on the same MR regardless of which SHA we ask from.
    assert db.finding_posted_on_mr("group/repo", 1, key) is True
    # Negatives: unknown key, blank key, and a different MR never match.
    assert db.finding_posted_on_mr("group/repo", 1, "nope") is False
    assert db.finding_posted_on_mr("group/repo", 1, "") is False
    assert db.finding_posted_on_mr("group/repo", 2, key) is False


def test_finding_posted_on_mr_ignores_non_live_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "DB", tmp_path / "reviewer.sqlite")
    db.init_db()
    skipped_key = finding_dedup_key("group/repo", 1, _finding(line=9))
    db.record_finding(
        project="group/repo",
        iid=1,
        sha="sha-A",
        fingerprint="fp-skip",
        finding=_finding(line=9),
        status=FindingStatus.SKIPPED,
        body="body",
        dedup_key=skipped_key,
    )
    # A SKIPPED finding is not on the thread, so it must not suppress a later post.
    assert db.finding_posted_on_mr("group/repo", 1, skipped_key) is False


def test_finding_posted_on_mr_matches_pending_external_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A PENDING_EXTERNAL_ID row means the comment was created but the provider
    # returned no id — it IS on the thread, so it must suppress a re-post.
    monkeypatch.setattr(paths, "DB", tmp_path / "reviewer.sqlite")
    db.init_db()
    key = finding_dedup_key("group/repo", 1, _finding())
    db.record_finding(
        project="group/repo",
        iid=1,
        sha="sha-A",
        fingerprint="fp-pending",
        finding=_finding(),
        status=FindingStatus.PENDING_EXTERNAL_ID,
        body="body",
        dedup_key=key,
    )
    assert db.finding_posted_on_mr("group/repo", 1, key) is True


# ---------------------------------------------------------------------------
# End-to-end via post_or_plan_findings — the actual bug
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Minimal GitLab-shaped provider that records every inline post."""

    name = "gitlab"

    def __init__(self) -> None:
        self.posts: list[tuple[int, object, str]] = []

    def change_number(self, change: dict) -> int:
        return int(change["iid"])

    def get_change(self, cfg, token, project, number):
        return {"iid": number}

    def changed_lines(self, cfg, token, project, number):
        return {
            "gradle.properties": {
                "old_path": "gradle.properties",
                "new_path": "gradle.properties",
                "new_lines": {3, 7},
            }
        }

    def build_position(self, change, changed, finding):
        file = finding.get("file") or finding.get("path")
        line = finding.get("line") or finding.get("new_line")
        entry = changed.get(file)
        if not entry or line not in entry["new_lines"]:
            return None
        return {"position_type": "text", "new_line": line}

    def post_inline_comment(self, cfg, token, project, number, body, position):
        self.posts.append((number, position.get("new_line"), body))
        return f"disc-{len(self.posts)}"


def _token_finding(title: str, line: int = 3, category: str = "security") -> dict[str, object]:
    return {
        "type": "issue",
        "severity": "blocking",
        "category": category,
        "file": "gradle.properties",
        "line": line,
        "title": title,
        "impact": "anyone with repo access can use the token",
        "evidence": "gradle.properties adds gitLabPrivateToken",
        "fix": "remove and rotate the token",
        "confidence": 0.99,
    }


def _review(provider: _FakeProvider, sha: str, findings: list[dict]) -> tuple[int, int, int]:
    return poller.post_or_plan_findings(
        cfg=ReviewConfig(gitlab_url="https://gitlab.com", dry_run=False),
        token="t",
        project="group/repo",
        mr={"iid": 1, "sha": sha},
        raw_review=json.dumps(findings),
        provider=provider,
    )


def test_reworded_finding_not_reposted_on_new_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "DB", tmp_path / "reviewer.sqlite")
    poller.init_db()
    provider = _FakeProvider()

    # Commit A: the token finding is posted once.
    posted_a, planned_a, skipped_a = _review(
        provider, "sha-A", [_token_finding("private token is committed")]
    )
    assert (posted_a, planned_a, skipped_a) == (1, 0, 0)

    # Commit B: same file/line/category, *reworded* body, new SHA. Must NOT
    # create a second thread — this is the regression.
    posted_b, _, skipped_b = _review(
        provider, "sha-B", [_token_finding("GitLab token is committed")]
    )
    assert posted_b == 0
    assert skipped_b == 1
    assert len(provider.posts) == 1  # exactly one thread across both commits


def test_distinct_finding_on_new_line_still_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "DB", tmp_path / "reviewer.sqlite")
    poller.init_db()
    provider = _FakeProvider()

    _review(provider, "sha-A", [_token_finding("private token is committed", line=3)])
    # A genuinely different finding (different line) on the next commit DOES post
    # — the safe direction: we never silently swallow a new issue.
    posted_b, _, _ = _review(
        provider, "sha-B", [_token_finding("hardcoded url", line=7, category="correctness")]
    )
    assert posted_b == 1
    assert len(provider.posts) == 2


def test_same_sha_retry_dedups_via_finding_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "DB", tmp_path / "reviewer.sqlite")
    poller.init_db()
    provider = _FakeProvider()

    _review(provider, "sha-A", [_token_finding("private token is committed")])
    # Re-running the identical review at the same SHA (a retried worker) still
    # dedups — the existing same-SHA guard is preserved, not regressed.
    posted_retry, _, skipped_retry = _review(
        provider, "sha-A", [_token_finding("private token is committed")]
    )
    assert posted_retry == 0
    assert skipped_retry == 1
    assert len(provider.posts) == 1
