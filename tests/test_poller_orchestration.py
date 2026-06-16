"""Poller orchestration: ``poll()``, ``check_health()``, ``worker()``.

These are the most critical and (until now) least directly-tested functions in
``bubo.poller`` — the cycle dispatcher, the liveness probe, and the per-change
review driver. Every external seam (provider, DB writers, the agent
subprocess, the finding-post path) is monkeypatched at the ``poller`` module
boundary so the tests exercise *control flow* — throttling, capping, target
filtering, cooperative shutdown, and the success/failure bookkeeping — without
network, subprocess, or MCP access.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from bubo import db, paths, poller
from bubo.config_values import ConfigError
from bubo.review_config import ReviewConfig
from bubo.statuses import ReviewStatus

# ---------------------------------------------------------------------------
# poll(): cycle dispatch
# ---------------------------------------------------------------------------


class _PollProvider:
    name = "fake"

    def __init__(self, changes: list[dict]) -> None:
        self._changes = changes

    def token(self) -> str:
        return "tok"

    def list_open_changes(self, cfg, project, token) -> list[dict]:
        return list(self._changes)

    def change_number(self, change) -> int:
        return int(change["iid"])


def _wire_poll(
    monkeypatch: Any,
    *,
    cfg: ReviewConfig,
    changes: list[dict],
    inflight: int = 0,
    seen: set[int] | None = None,
    shutdown_after: int | None = None,
) -> dict[str, list]:
    """Wire poll()'s seams; return a dict capturing recorded + forked changes."""
    captured: dict[str, list] = {"recorded": [], "forked": []}
    seen = seen or set()
    state = {"checks": 0}

    monkeypatch.setattr(poller, "init_db", lambda: None)
    monkeypatch.setattr(poller, "read_config", lambda: cfg)
    monkeypatch.setattr(poller, "get_provider", lambda c: _PollProvider(changes))
    monkeypatch.setattr(poller, "count_inflight_workers", lambda: inflight)
    monkeypatch.setattr(
        poller, "already_seen", lambda project, number, sha, **k: number in seen
    )
    monkeypatch.setattr(
        poller,
        "record",
        lambda project, number, sha, status, *a: captured["recorded"].append((number, status)),
    )
    monkeypatch.setattr(poller, "write_job", lambda project, change: Path("/tmp/job.json"))
    monkeypatch.setattr(
        poller, "fork_worker", lambda job: captured["forked"].append(job) or 4321
    )

    def _shutdown() -> bool:
        state["checks"] += 1
        return shutdown_after is not None and state["checks"] > shutdown_after

    monkeypatch.setattr(poller, "_shutdown_requested", _shutdown)
    return captured


def test_poll_throttles_when_inflight_at_cap(monkeypatch: Any) -> None:
    cfg = ReviewConfig(projects=["g/p"], max_merge_requests_per_poll=2)
    cap = cfg.max_merge_requests_per_poll * poller.INFLIGHT_WORKER_MULTIPLIER
    cap_state = _wire_poll(
        monkeypatch, cfg=cfg, changes=[{"iid": 1, "sha": "a"}], inflight=cap
    )

    assert poller.poll() == 0
    assert cap_state["forked"] == []  # never forked when throttled


def test_poll_forks_each_eligible_change(monkeypatch: Any) -> None:
    cfg = ReviewConfig(projects=["g/p"], max_merge_requests_per_poll=5)
    changes = [{"iid": 1, "sha": "a"}, {"iid": 2, "sha": "b"}]
    cap_state = _wire_poll(monkeypatch, cfg=cfg, changes=changes)

    assert poller.poll() == 2
    assert len(cap_state["forked"]) == 2
    assert [n for n, s in cap_state["recorded"]] == [1, 2]
    assert all(s == ReviewStatus.QUEUED for _, s in cap_state["recorded"])


def test_poll_caps_per_cycle(monkeypatch: Any) -> None:
    cfg = ReviewConfig(projects=["g/p"], max_merge_requests_per_poll=2)
    changes = [{"iid": i, "sha": f"s{i}"} for i in range(1, 6)]
    cap_state = _wire_poll(monkeypatch, cfg=cfg, changes=changes)

    assert poller.poll() == 2  # stopped at the per-cycle cap
    assert len(cap_state["forked"]) == 2


def test_poll_target_iid_filter(monkeypatch: Any) -> None:
    cfg = ReviewConfig(
        projects=["g/p"], max_merge_requests_per_poll=5, target_merge_request_iid=2
    )
    changes = [{"iid": 1, "sha": "a"}, {"iid": 2, "sha": "b"}, {"iid": 3, "sha": "c"}]
    cap_state = _wire_poll(monkeypatch, cfg=cfg, changes=changes)

    assert poller.poll() == 1
    assert [n for n, s in cap_state["recorded"]] == [2]


def test_poll_skips_already_seen(monkeypatch: Any) -> None:
    cfg = ReviewConfig(projects=["g/p"], max_merge_requests_per_poll=5)
    changes = [{"iid": 1, "sha": "a"}, {"iid": 2, "sha": "b"}]
    cap_state = _wire_poll(monkeypatch, cfg=cfg, changes=changes, seen={1})

    assert poller.poll() == 1
    assert [n for n, s in cap_state["recorded"]] == [2]


def test_poll_skips_change_without_sha(monkeypatch: Any) -> None:
    cfg = ReviewConfig(projects=["g/p"], max_merge_requests_per_poll=5)
    changes = [{"iid": 1}, {"iid": 2, "sha": "b"}]  # first has no head sha
    cap_state = _wire_poll(monkeypatch, cfg=cfg, changes=changes)

    assert poller.poll() == 1
    assert [n for n, s in cap_state["recorded"]] == [2]


def test_poll_stops_on_shutdown(monkeypatch: Any) -> None:
    cfg = ReviewConfig(projects=["g/p"], max_merge_requests_per_poll=5)
    changes = [{"iid": i, "sha": f"s{i}"} for i in range(1, 6)]
    # shutdown flips true after the project-loop check + first change check,
    # so exactly one change is queued before the loop bails.
    cap_state = _wire_poll(monkeypatch, cfg=cfg, changes=changes, shutdown_after=2)

    assert poller.poll() == 1
    assert len(cap_state["forked"]) == 1


# ---------------------------------------------------------------------------
# check_health(): liveness probe
# ---------------------------------------------------------------------------


def test_health_returns_2_on_config_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(poller, "init_db", lambda: None)

    def _boom() -> ReviewConfig:
        raise ConfigError("bad config")

    monkeypatch.setattr(poller, "read_config", _boom)
    assert poller.check_health() == 2


def test_health_empty_db_is_ok(monkeypatch: Any) -> None:
    monkeypatch.setattr(poller, "init_db", lambda: None)
    monkeypatch.setattr(poller, "read_config", lambda: ReviewConfig(timeout_seconds=100))
    monkeypatch.setattr(poller, "latest_reviewed_row", lambda: None)
    assert poller.check_health() == 0


def test_health_fresh_row_is_ok(monkeypatch: Any) -> None:
    monkeypatch.setattr(poller, "init_db", lambda: None)
    monkeypatch.setattr(poller, "read_config", lambda: ReviewConfig(timeout_seconds=100))
    monkeypatch.setattr(poller, "latest_reviewed_row", lambda: ("success", "ts"))
    monkeypatch.setattr(db, "status_age_seconds", lambda updated_at: 10.0)
    assert poller.check_health() == 0


def test_health_stale_row_fails(monkeypatch: Any) -> None:
    monkeypatch.setattr(poller, "init_db", lambda: None)
    monkeypatch.setattr(poller, "read_config", lambda: ReviewConfig(timeout_seconds=100))
    monkeypatch.setattr(poller, "latest_reviewed_row", lambda: ("success", "ts"))
    # threshold = 100 * 3 = 300; 9999 is well past it.
    monkeypatch.setattr(db, "status_age_seconds", lambda updated_at: 9999.0)
    assert poller.check_health() == 1


# ---------------------------------------------------------------------------
# worker(): per-change review driver (end-to-end, all seams faked)
# ---------------------------------------------------------------------------


class _WorkerProvider:
    name = "fake"

    def token(self) -> str:
        return "tok"

    def checkout(self, cfg, project, mr, repo) -> None:
        Path(repo).mkdir(parents=True, exist_ok=True)

    def review_prompt(self, project, mr, cfg, extra_directive="") -> str:
        return "PROMPT"


@contextmanager
def _worker_sandbox() -> Iterator[Path]:
    """Point all poller paths at a temp tree and init the DB."""
    saved = (paths.DB, paths.WORK, paths.REPORTS, paths.JOBS)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths.DB = root / "state" / "reviewer.sqlite"
            paths.WORK = root / "work"
            paths.REPORTS = root / "reports"
            paths.JOBS = root / "jobs"
            paths.JOBS.mkdir(parents=True, exist_ok=True)
            poller.init_db()
            yield root
    finally:
        paths.DB, paths.WORK, paths.REPORTS, paths.JOBS = saved


def _write_job(root: Path) -> Path:
    job = paths.JOBS / "g-p-7-deadbeefcafe.json"
    job.write_text(
        json.dumps(
            {"project": "g/p", "mr": {"iid": 7, "sha": "deadbeefcafe1234"}, "queued_at": ""}
        )
    )
    return job


def _wire_worker(monkeypatch: Any, *, returncode: int) -> None:
    cfg = ReviewConfig(dry_run=True)
    monkeypatch.setattr(poller, "read_config", lambda: cfg)
    monkeypatch.setattr(poller, "get_provider", lambda c: _WorkerProvider())
    monkeypatch.setattr(poller, "write_rendered_meta_prompt", lambda c: paths.REPORTS / "meta.md")
    monkeypatch.setattr(poller, "prompt_version", lambda p: "v-test")
    monkeypatch.setattr(
        poller,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=returncode, stdout="[]"),
    )
    monkeypatch.setattr(poller, "post_or_plan_findings", lambda **k: (1, 0, 0))


def _status_row() -> str:
    with sqlite3.connect(paths.DB) as conn:
        row = conn.execute("select status from reviewed_mrs").fetchone()
    return row[0]


def test_worker_success_records_success(monkeypatch: Any) -> None:
    with _worker_sandbox() as root:
        paths.REPORTS.mkdir(parents=True, exist_ok=True)
        _wire_worker(monkeypatch, returncode=0)
        job = _write_job(root)

        assert poller.worker(job) == 0
        assert _status_row() == ReviewStatus.SUCCESS.value

        with sqlite3.connect(paths.DB) as conn:
            run_status = conn.execute("select status from review_runs").fetchone()
        assert run_status[0] == ReviewStatus.SUCCESS.value


def test_worker_nonzero_agent_exit_records_failed(monkeypatch: Any) -> None:
    with _worker_sandbox() as root:
        paths.REPORTS.mkdir(parents=True, exist_ok=True)
        _wire_worker(monkeypatch, returncode=3)  # agent exits nonzero → RuntimeError
        job = _write_job(root)

        assert poller.worker(job) == 1
        assert _status_row() == ReviewStatus.FAILED.value
