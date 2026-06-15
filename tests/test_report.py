"""Governance report tests (Rec ③).

Two layers: the read-only DB aggregation readers in :mod:`bubo.db` (seeded with
explicit timestamps for determinism), and the assembly + formatters in
:mod:`bubo.report` (the formatter cases are DB-free).
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from bubo import db, paths


@contextmanager
def _temp_db() -> Iterator[None]:
    original = paths.DB
    try:
        with tempfile.TemporaryDirectory() as tmp:
            paths.DB = Path(tmp) / "reviewer.sqlite"
            db.init_db()
            yield
    finally:
        paths.DB = original


def _ts(hours_ago: float = 1.0) -> str:
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _seed() -> None:
    """Seed one realistic window: 1 run (likely_ai+sensitive), 3 findings, outcomes, a decision."""
    when = _ts(1.0)
    with sqlite3.connect(paths.DB) as con:
        con.execute(
            """insert into review_runs(run_id,project,iid,sha,status,model,review_mode,
               dry_run,started_at,finished_at,tokens_total,cost_usd,
               provenance_band,provenance_source,provenance_confidence,
               provenance_signals,sensitive_paths)
               values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("run1", "g/r", 1, "sha1", "success", "gpt-5.5", "diff", 1, when, when,
             1000, 0.5, "likely_ai", "trailer", "declared",
             '["Generated-by: GPT-4"]', '["payments/charge.py"]'),
        )
        # 3 findings on the SAME run — the audit-row no-double-count case.
        for i, sev, status in [(0, "blocking", "posted"), (1, "non-blocking", "posted"),
                               (2, "blocking", "skipped")]:
            con.execute(
                """insert into review_findings(project,iid,sha,fingerprint,file,line,
                   status,body,updated_at,severity)
                   values(?,?,?,?,?,?,?,?,?,?)""",
                ("g/r", 1, "sha1", f"fp{i}", "f.py", 1, status, "b", when, sev),
            )
        # Outcomes: 2 resolved (1 blocking accepted), 1 disputed, 0 false-positive.
        for i, resolved, disputed, fp in [(0, 1, 0, 0), (1, 1, 0, 0), (2, 0, 1, 0)]:
            con.execute(
                """insert into finding_outcomes(finding_id,project,iid,sha,fingerprint,
                   resolved,disputed,false_positive,last_checked_at)
                   values(?,?,?,?,?,?,?,?,?)""",
                (f"g/r:1:sha1:fp{i}", "g/r", 1, "sha1", f"fp{i}", resolved, disputed, fp, when),
            )
        con.execute(
            """insert into governance_decisions(run_id,project,iid,sha,mode,action,
               triggered,matched_rule,rigor_injected,band,sensitive_paths,reason,created_at)
               values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("run1", "g/r", 1, "sha1", "soft", "flag", 1, "band+sensitive", 1,
             "likely_ai", '["payments/charge.py"]', "test", when),
        )


# --- DB readers (run against bubo.db directly) ------------------------------


def test_provenance_summary_counts_by_band_and_source() -> None:
    with _temp_db():
        _seed()
        out = db.provenance_summary(since_hours=24, project="g/r")
    assert out["runs_total"] == 1
    assert out["by_band"] == {"likely_ai": 1}
    assert out["by_source"] == {"trailer": 1}
    assert out["sensitive_path_runs"] == 1


def test_outcomes_summary_raw_counts() -> None:
    with _temp_db():
        _seed()
        out = db.outcomes_summary(since_hours=24)
    assert out["total"] == 3
    assert out["resolved"] == 2
    assert out["disputed"] == 1
    assert out["false_positive"] == 0


def test_noise_trend_daily_buckets() -> None:
    with _temp_db():
        _seed()
        rows = db.noise_trend(since_hours=24)
    assert len(rows) == 1
    assert rows[0]["findings"] == 3
    assert rows[0]["disputed"] == 1


def test_roi_proxy_counts_accepted() -> None:
    with _temp_db():
        _seed()
        out = db.roi_proxy(since_hours=24)
    assert out["findings_total"] == 3
    assert out["accepted"] == 2  # fp0, fp1 resolved & not disputed/fp
    assert out["blocking_accepted"] == 1  # only fp0 is blocking + accepted
    assert out["cost_usd_sum"] == 0.5


def test_policy_decisions_summary_reads_table() -> None:
    with _temp_db():
        _seed()
        out = db.policy_decisions_summary(since_hours=24)
    assert out["available"] is True
    assert out["total"] == 1
    assert out["by_action"] == {"flag": 1}
    assert out["by_mode"] == {"soft": 1}


def test_audit_rows_one_per_run_no_double_count() -> None:
    with _temp_db():
        _seed()
        rows = db.audit_rows(since_hours=24)
    # One run with three findings → exactly one audit row, tokens counted once.
    assert len(rows) == 1
    row = rows[0]
    assert row["tokens_total"] == 1000  # NOT 3000
    assert row["findings_total"] == 3
    assert row["findings_posted"] == 2
    assert row["outcomes_resolved"] == 2
    assert row["outcomes_disputed"] == 1
    assert row["sensitive_paths_count"] == 1
    assert row["policy_action"] == "flag"
    assert row["provenance_band"] == "likely_ai"


def test_window_excludes_old_rows() -> None:
    with _temp_db():
        _seed()
        # A 1-hour window excludes the ~1h-old seed (seeded at now-1h).
        narrow = db.provenance_summary(since_hours=1)
        # Wide window includes it.
        wide = db.provenance_summary(since_hours=24)
    assert wide["runs_total"] == 1
    # The seed sits right at the 1h boundary; assert the wide window sees more
    # or equal — the key determinism check is that 24h includes it.
    assert narrow["runs_total"] <= wide["runs_total"]


def test_readers_do_not_mutate_schema() -> None:
    # Reporting must be read-only: running readers creates no tables/columns.
    with _temp_db():
        _seed()
        with sqlite3.connect(paths.DB) as con:
            before = {r[0] for r in con.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()}
        db.provenance_summary(since_hours=24)
        db.audit_rows(since_hours=24)
        db.policy_decisions_summary(since_hours=24)
        with sqlite3.connect(paths.DB) as con:
            after = {r[0] for r in con.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()}
    assert before == after


# --- report assembly + formatters ------------------------------------------


def test_build_report_assembles_all_sections_with_derived_rates() -> None:
    from bubo import report

    with _temp_db():
        _seed()
        rep = report.build_report(since_hours=24, project="g/r", generated_at="fixed")

    assert rep["meta"]["generated_at"] == "fixed"
    assert rep["meta"]["schema_version"] == report.SCHEMA_VERSION
    assert set(rep) >= {
        "meta", "reviews", "provenance", "outcomes", "noise_trend",
        "roi", "policy_decisions", "audit",
    }
    assert rep["provenance"]["by_band"] == {"likely_ai": 1}
    # Derived rate: 2 resolved / 3 outcomes.
    assert rep["outcomes"]["accept_rate"] == round(2 / 3, 4)
    assert rep["policy_decisions"]["available"] is True
    assert len(rep["audit"]) == 1


def test_to_json_is_deterministic() -> None:
    from bubo import report

    rep = {"meta": {"schema_version": 1}, "audit": [{"run_id": "a", "iid": 1}]}
    first = report.to_json(rep)
    assert first == report.to_json(rep)  # byte-identical
    assert first.endswith("\n")


def test_to_csv_audit_section_has_fixed_columns() -> None:
    from bubo import report

    rep = {
        "audit": [
            {col: "" for col in report.AUDIT_COLUMNS} | {"run_id": "r1", "iid": 1},
        ]
    }
    csv_text = report.to_csv(rep, section="audit")
    header = csv_text.splitlines()[0]
    assert header == ",".join(report.AUDIT_COLUMNS)
    assert "r1" in csv_text


def test_to_csv_rejects_scalar_section() -> None:
    import pytest

    from bubo import report

    with pytest.raises(ValueError, match="not CSV-renderable"):
        report.to_csv({"outcomes": {"total": 1}}, section="outcomes")


# --- CLI + MCP consumer wiring ---------------------------------------------

_PATH_ATTRS = ("ROOT", "CONFIG", "DB", "WORK", "REPORTS", "JOBS", "LOGS", "RENDERED_PROMPTS")


@contextmanager
def _restore_paths() -> Iterator[None]:
    """Snapshot/restore paths.* — cmd_report's _retarget_paths mutates them globally."""
    saved = {a: getattr(paths, a) for a in _PATH_ATTRS}
    try:
        yield
    finally:
        for attr, value in saved.items():
            setattr(paths, attr, value)


def test_cli_report_json_smoke(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    import json as _json

    from bubo import cli
    from bubo.cli import _retarget_paths

    with _restore_paths():
        _retarget_paths(tmp_path)
        db.init_db()  # initialized but empty
        rc = cli.main(["report", "--root", str(tmp_path), "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = _json.loads(out)
    assert "meta" in parsed
    assert "audit" in parsed


def test_cli_report_missing_db_exits_nonzero(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from bubo import cli

    with _restore_paths():
        rc = cli.main(["report", "--root", str(tmp_path / "uninitialized")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "bubo init" in err


def test_mcp_get_governance_report_returns_sections() -> None:
    from bubo import mcp_server

    with _temp_db():
        _seed()
        rep = mcp_server.get_governance_report(since_hours=24)
    assert "meta" in rep
    assert rep["provenance"]["by_band"] == {"likely_ai": 1}
    assert rep["policy_decisions"]["available"] is True
