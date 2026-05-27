from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from llm_reviewer import poller
from llm_reviewer.telemetry.config import TelemetryConfig


def test_classify_discussion_outcome_uses_explicit_manual_markers() -> None:
    discussion = {
        "id": "disc1",
        "resolved": True,
        "notes": [
            {"author": {"username": "llm-reviewer"}, "body": "finding"},
            {"author": {"username": "dev1"}, "body": "[llm-review:false-positive] not an issue"},
            {"author": {"username": "dev2"}, "body": "[llm-review:duplicate] already covered"},
        ],
    }

    outcome = poller.classify_discussion_outcome(discussion, bot_username="llm-reviewer", mr_state="merged")

    assert outcome["resolved"] is True
    assert outcome["developer_replied"] is True
    assert outcome["false_positive"] is True
    assert outcome["duplicate"] is True
    assert outcome["merged_unresolved"] is False


def test_classify_discussion_outcome_marks_unresolved_after_merge() -> None:
    discussion = {"id": "disc1", "resolved": False, "notes": []}

    outcome = poller.classify_discussion_outcome(discussion, bot_username="llm-reviewer", mr_state="merged")

    assert outcome["resolved"] is False
    assert outcome["merged_unresolved"] is True


def test_outcome_sync_flag_suppresses_outcome_metric_emission() -> None:
    class FakeTelemetry:
        config = TelemetryConfig(enabled=True, emit_outcome_sync=False)

        def __init__(self) -> None:
            self.finding_metrics = 0

        def record_finding(self, **_: object) -> None:
            self.finding_metrics += 1

        def record_failure(self, **_: object) -> None:
            raise AssertionError("unexpected failure metric")

    original_db = poller.DB
    try:
        with tempfile.TemporaryDirectory() as tmp:
            poller.DB = Path(tmp) / "reviewer.sqlite"
            poller.init_db()
            with sqlite3.connect(poller.DB) as db:
                db.execute(
                    """
                    insert into review_findings(project,iid,sha,fingerprint,file,line,status,discussion_id,body,updated_at)
                    values(?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("group/repo", 1, "sha", "fp", "src/A.py", 1, "posted", "disc", "body", poller.now()),
                )
            fake = FakeTelemetry()
            cfg = {"telemetry_config": fake.config, "gitlab_url": "https://gitlab.com"}

            with patch("llm_reviewer.poller.read_config", return_value=cfg):
                with patch("llm_reviewer.poller.gitlab_token", return_value="token"):
                    with patch("llm_reviewer.poller.ReviewTelemetry.from_config", return_value=fake):
                        with patch("llm_reviewer.poller.get_mr", return_value={"state": "merged"}):
                            with patch(
                                "llm_reviewer.poller.get_mr_discussion",
                                return_value={"id": "disc", "resolved": True, "notes": []},
                            ):
                                assert poller.sync_outcomes(limit=1) == 1

            assert fake.finding_metrics == 0
    finally:
        poller.DB = original_db
