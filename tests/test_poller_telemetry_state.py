from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from llm_reviewer import poller
from llm_reviewer.telemetry.config import TelemetryConfig
from llm_reviewer.telemetry.cost import TokenUsage


def test_init_db_creates_review_telemetry_state_tables() -> None:
    original_db = poller.DB
    try:
        with tempfile.TemporaryDirectory() as tmp:
            poller.DB = Path(tmp) / "reviewer.sqlite"
            poller.init_db()

            with sqlite3.connect(poller.DB) as db:
                tables = {
                    row[0]
                    for row in db.execute(
                        "select name from sqlite_master where type='table'"
                    ).fetchall()
                }
                finding_columns = {
                    row[1]
                    for row in db.execute("pragma table_info(review_findings)").fetchall()
                }

            assert {"review_runs", "finding_outcomes"}.issubset(tables)
            assert {"run_id", "type", "severity", "category", "confidence", "note_id"}.issubset(finding_columns)
    finally:
        poller.DB = original_db


def test_empty_gitlab_discussion_id_is_not_marked_posted() -> None:
    original_db = poller.DB
    try:
        with tempfile.TemporaryDirectory() as tmp:
            poller.DB = Path(tmp) / "reviewer.sqlite"
            poller.init_db()
            cfg = {
                "gitlab_url": "https://gitlab.com",
                "dry_run": False,
                "max_findings_per_review": 5,
            }
            mr = {
                "iid": 9,
                "sha": "abc",
                "diff_refs": {"base_sha": "b", "start_sha": "s", "head_sha": "h"},
            }
            raw = """[{
              "type":"issue",
              "severity":"blocking",
              "category":"correctness",
              "title":"bad",
              "file":"src/A.java",
              "line":12,
              "impact":"i",
              "evidence":"e",
              "fix":"f",
              "confidence":1
            }]"""
            diff = {
                "new_path": "src/A.java",
                "old_path": "src/A.java",
                "diff": "@@ -10,1 +12,1 @@\n+new\n",
            }

            with patch("llm_reviewer.poller.get_mr", return_value=mr):
                with patch("llm_reviewer.poller.get_mr_diffs", return_value=[diff]):
                    with patch("llm_reviewer.poller.post_inline_finding", return_value=""):
                        posted, planned, skipped = poller.post_or_plan_findings(
                            cfg,
                            "token",
                            "group/repo",
                            mr,
                            raw,
                            run_id="run1",
                        )

            with sqlite3.connect(poller.DB) as db:
                row = db.execute("select status,discussion_id from review_findings").fetchone()

            assert (posted, planned, skipped) == (0, 0, 1)
            assert row == ("pending_external_id", None)
    finally:
        poller.DB = original_db


def test_finding_metric_flag_suppresses_finding_emission() -> None:
    class FakeTelemetry:
        config = TelemetryConfig(enabled=True, emit_finding_events=False)

        def __init__(self) -> None:
            self.count = 0

        def record_finding(self, **_: object) -> None:
            self.count += 1

    original_db = poller.DB
    try:
        with tempfile.TemporaryDirectory() as tmp:
            poller.DB = Path(tmp) / "reviewer.sqlite"
            poller.init_db()
            telemetry = FakeTelemetry()
            cfg = {
                "gitlab_url": "https://gitlab.com",
                "dry_run": True,
                "max_findings_per_review": 5,
            }
            mr = {
                "iid": 9,
                "sha": "abc",
                "diff_refs": {"base_sha": "b", "start_sha": "s", "head_sha": "h"},
            }
            raw = '[{"type":"issue","severity":"blocking","category":"correctness","title":"bad","file":"src/A.java","line":12}]'
            diff = {
                "new_path": "src/A.java",
                "old_path": "src/A.java",
                "diff": "@@ -10,1 +12,1 @@\n+new\n",
            }

            with patch("llm_reviewer.poller.get_mr", return_value=mr):
                with patch("llm_reviewer.poller.get_mr_diffs", return_value=[diff]):
                    poller.post_or_plan_findings(
                        cfg,
                        "token",
                        "group/repo",
                        mr,
                        raw,
                        telemetry=telemetry,
                    )

            assert telemetry.count == 0
    finally:
        poller.DB = original_db


def test_outcome_sync_prefers_never_checked_then_oldest_checked() -> None:
    original_db = poller.DB
    try:
        with tempfile.TemporaryDirectory() as tmp:
            poller.DB = Path(tmp) / "reviewer.sqlite"
            poller.init_db()
            findings = [
                ("group/repo", 1, "sha", "old", "src/A.py", 1, "posted", "disc-old", "body", "2026-01-01T00:00:00+00:00"),
                ("group/repo", 1, "sha", "new", "src/B.py", 2, "posted", "disc-new", "body", "2026-01-02T00:00:00+00:00"),
                ("group/repo", 1, "sha", "never", "src/C.py", 3, "posted", "disc-never", "body", "2026-01-03T00:00:00+00:00"),
            ]
            with poller.connect_db() as db:
                db.executemany(
                    """
                    insert into review_findings(project,iid,sha,fingerprint,file,line,status,discussion_id,body,updated_at)
                    values(?,?,?,?,?,?,?,?,?,?)
                    """,
                    findings,
                )
                db.execute(
                    """
                    insert into finding_outcomes(
                      finding_id,project,iid,sha,fingerprint,discussion_id,last_checked_at
                    )
                    values(?,?,?,?,?,?,?)
                    """,
                    (
                        "group/repo:1:sha:old",
                        "group/repo",
                        1,
                        "sha",
                        "old",
                        "disc-old",
                        "2026-01-01T01:00:00+00:00",
                    ),
                )

            rows = poller.posted_findings_for_outcome_sync(limit=3)

            assert [row["fingerprint"] for row in rows] == ["new", "never", "old"]
    finally:
        poller.DB = original_db


def test_record_review_run_start_and_finish() -> None:
    original_db = poller.DB
    try:
        with tempfile.TemporaryDirectory() as tmp:
            poller.DB = Path(tmp) / "reviewer.sqlite"
            poller.init_db()

            run_id = poller.review_run_id("group/repo", 7, "abc")
            poller.record_review_run_start(
                run_id=run_id,
                project="group/repo",
                iid=7,
                sha="abc",
                model="codex-cli",
                prompt_version="prompt1",
                review_mode="diff",
                dry_run=True,
            )
            poller.record_review_run_finish(
                run_id=run_id,
                status="success",
                tokens=TokenUsage(input=10, output=2, cached=1, total=13),
                cost_usd=0.25,
                error=None,
            )

            with sqlite3.connect(poller.DB) as db:
                row = db.execute(
                    "select status,tokens_input,tokens_output,tokens_cached,tokens_total,cost_usd from review_runs where run_id=?",
                    (run_id,),
                ).fetchone()

            assert row == ("success", 10, 2, 1, 13, 0.25)
    finally:
        poller.DB = original_db
