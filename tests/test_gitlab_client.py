"""GitLab REST client (``bubo.gitlab``).

The HTTP layer (:func:`bubo._http.request_json`) is exercised elsewhere; here
we fake :func:`bubo.gitlab.api` so the tests cover what this module actually
owns: URL/auth construction, pagination, the JSON-shape guards, and the two
load-bearing pure functions — ``find_note_by_body`` (bot-scoped dedup) and
``classify_discussion_outcome`` (the outcome-sync state machine).
"""

from __future__ import annotations

from typing import Any

import pytest

from bubo import gitlab
from bubo.review_config import ReviewConfig

CFG = ReviewConfig(gitlab_url="https://gl.example")
TOKEN = "glpat-xxx"


# --- api_pages: pagination + shape guard -----------------------------------


def test_api_pages_follows_x_next_page_and_concatenates(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_api(base, token, method, path, body=None):
        calls.append(path)
        assert method == "GET"
        assert "per_page=100" in path
        # Match the page param with its "&" boundary: a bare "page=1" substring
        # ALSO matches "per_page=100", which would make every page advertise a
        # next page and loop api_pages() forever (out grows without bound).
        if "&page=1" in path:
            return [{"id": 1}, {"id": 2}], {"X-Next-Page": "2"}
        return [{"id": 3}], {"X-Next-Page": ""}

    monkeypatch.setattr(gitlab, "api", fake_api)
    out = gitlab.api_pages(CFG.gitlab_url, TOKEN, "/projects/x/merge_requests")

    assert [item["id"] for item in out] == [1, 2, 3]
    assert len(calls) == 2  # stopped once X-Next-Page was empty


def test_api_pages_skips_non_dict_items(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        gitlab, "api", lambda *a, **k: ([{"id": 1}, "junk", 7, {"id": 2}], {})
    )
    out = gitlab.api_pages(CFG.gitlab_url, TOKEN, "/p")
    assert [item["id"] for item in out] == [1, 2]


def test_api_pages_raises_when_page_is_not_a_list(monkeypatch: Any) -> None:
    monkeypatch.setattr(gitlab, "api", lambda *a, **k: ({"not": "a list"}, {}))
    with pytest.raises(RuntimeError, match="did not return a list"):
        gitlab.api_pages(CFG.gitlab_url, TOKEN, "/p")


# --- thin wrappers: path + method + body -----------------------------------


def test_get_mr_builds_encoded_path_and_returns_object(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_api(base, token, method, path, body=None):
        seen.update(base=base, method=method, path=path, body=body)
        return {"iid": 7, "state": "opened"}, {}

    monkeypatch.setattr(gitlab, "api", fake_api)
    mr = gitlab.get_mr(CFG, TOKEN, "group/sub/proj", 7)

    assert mr["iid"] == 7
    assert seen["method"] == "GET"
    assert seen["path"] == "/projects/group%2Fsub%2Fproj/merge_requests/7"
    assert seen["body"] is None


def test_get_mr_raises_when_response_not_object(monkeypatch: Any) -> None:
    monkeypatch.setattr(gitlab, "api", lambda *a, **k: ([1, 2], {}))
    with pytest.raises(RuntimeError, match="not an object"):
        gitlab.get_mr(CFG, TOKEN, "p", 1)


def test_create_mr_note_posts_body(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_api(base, token, method, path, body=None):
        seen.update(method=method, path=path, body=body)
        return {"id": 99}, {}

    monkeypatch.setattr(gitlab, "api", fake_api)
    note = gitlab.create_mr_note(CFG, TOKEN, "p", 3, "all good")

    assert note["id"] == 99
    assert seen["method"] == "POST"
    assert seen["path"].endswith("/merge_requests/3/notes")
    assert seen["body"] == {"body": "all good"}


def test_create_mr_note_returns_empty_on_non_dict(monkeypatch: Any) -> None:
    monkeypatch.setattr(gitlab, "api", lambda *a, **k: ("oops", {}))
    assert gitlab.create_mr_note(CFG, TOKEN, "p", 1, "x") == {}


def test_create_discussion_sends_body_and_position(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_api(base, token, method, path, body=None):
        seen.update(method=method, body=body)
        return {"id": "disc-1"}, {}

    monkeypatch.setattr(gitlab, "api", fake_api)
    pos = {"new_path": "f.py", "new_line": 10}
    out = gitlab.create_merge_request_discussion(CFG, TOKEN, "p", 4, "body", pos)

    assert out["id"] == "disc-1"
    assert seen["method"] == "POST"
    assert seen["body"] == {"body": "body", "position": pos}


def test_open_mrs_filters_opened_scope_all(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        gitlab, "api_pages", lambda base, token, path: seen.setdefault("path", path) or []
    )
    gitlab.open_mrs(CFG, "grp/proj", TOKEN)
    assert "state=opened" in seen["path"]
    assert "scope=all" in seen["path"]
    assert "/projects/grp%2Fproj/merge_requests" in seen["path"]


# --- find_note_by_body: bot-scoped dedup -----------------------------------


def _notes_provider(monkeypatch: Any, notes: list[dict]) -> None:
    monkeypatch.setattr(gitlab, "get_mr_notes", lambda *a, **k: notes)


def test_find_note_by_body_matches_bot_note(monkeypatch: Any) -> None:
    _notes_provider(
        monkeypatch,
        [{"id": 5, "body": "ack", "author": {"username": "bubo-bot"}}],
    )
    assert gitlab.find_note_by_body(CFG, TOKEN, "p", 1, "ack", bot_username="bubo-bot") == "5"


def test_find_note_by_body_ignores_other_author(monkeypatch: Any) -> None:
    _notes_provider(
        monkeypatch,
        [{"id": 5, "body": "ack", "author": {"username": "human"}}],
    )
    assert gitlab.find_note_by_body(CFG, TOKEN, "p", 1, "ack", bot_username="bubo-bot") == ""


def test_find_note_by_body_skips_system_notes(monkeypatch: Any) -> None:
    _notes_provider(
        monkeypatch,
        [{"id": 5, "body": "ack", "system": True, "author": {"username": "bubo-bot"}}],
    )
    assert gitlab.find_note_by_body(CFG, TOKEN, "p", 1, "ack", bot_username="bubo-bot") == ""


def test_find_discussion_by_body_returns_id(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        gitlab,
        "api_pages",
        lambda *a, **k: [{"id": "d1", "notes": [{"body": "hello"}]}],
    )
    assert gitlab.find_discussion_by_body(CFG, TOKEN, "p", 1, "hello") == "d1"
    assert gitlab.find_discussion_by_body(CFG, TOKEN, "p", 1, "absent") == ""


# --- classify_discussion_outcome: the outcome state machine ----------------

BOT = "bubo-bot"


def test_outcome_resolved_via_discussion_flag() -> None:
    out = gitlab.classify_discussion_outcome(
        {"resolved": True, "resolved_at": "2026-01-01", "notes": []}, BOT, "opened"
    )
    assert out["resolved"] is True
    assert out["resolved_at"] == "2026-01-01"
    assert out["merged_unresolved"] is False


def test_outcome_resolved_via_note_resolvable() -> None:
    out = gitlab.classify_discussion_outcome(
        {"notes": [{"resolvable": True, "resolved": True, "author": {"username": BOT}}]},
        BOT,
        "opened",
    )
    assert out["resolved"] is True


def test_outcome_developer_reply_and_finding_text() -> None:
    out = gitlab.classify_discussion_outcome(
        {
            "notes": [
                {"body": "Issue: bug", "author": {"username": BOT}},
                {"body": "thanks, fixing", "author": {"username": "dev"}},
            ]
        },
        BOT,
        "opened",
    )
    assert out["developer_replied"] is True
    assert out["_finding_text"] == "Issue: bug"
    assert out["_reply_text"] == "thanks, fixing"


def test_outcome_dispute_markers() -> None:
    out = gitlab.classify_discussion_outcome(
        {
            "notes": [
                {"body": "Issue: x", "author": {"username": BOT}},
                {"body": "nope [llm-review:false-positive]", "author": {"username": "dev"}},
            ]
        },
        BOT,
        "opened",
    )
    assert out["false_positive"] is True
    assert out["disputed"] is True
    assert out["duplicate"] is False


def test_outcome_duplicate_marker() -> None:
    out = gitlab.classify_discussion_outcome(
        {"notes": [{"body": "[llm-review:duplicate]", "author": {"username": "dev"}}]},
        BOT,
        "opened",
    )
    assert out["duplicate"] is True


def test_outcome_deleted_when_all_notes_deleted() -> None:
    out = gitlab.classify_discussion_outcome(
        {"notes": [{"body": "gone", "deleted": True, "author": {"username": BOT}}]},
        BOT,
        "opened",
    )
    assert out["deleted"] is True


def test_outcome_merged_unresolved() -> None:
    out = gitlab.classify_discussion_outcome(
        {"resolved": False, "notes": [{"body": "x", "author": {"username": BOT}}]},
        BOT,
        "merged",
    )
    assert out["merged_unresolved"] is True
