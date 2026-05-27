# OTel Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenTelemetry-based review metrics and GitLab outcome correlation without creating a separate analytics database.

**Architecture:** The package emits OpenTelemetry metrics, spans, and events through one telemetry module. `reviewer.sqlite` remains operational state for review idempotency and GitLab discussion correlation only. Rollups are handled by the OTel backend or Collector, not by Python tables.

**Tech Stack:** Python 3.14, `uv`, SQLite, GitLab API, OpenTelemetry API/SDK/OTLP exporter, pytest.

---

### Task 1: Telemetry Core

**Files:**
- Create: `src/llm_reviewer/telemetry/__init__.py`
- Create: `src/llm_reviewer/telemetry/config.py`
- Create: `src/llm_reviewer/telemetry/cost.py`
- Create: `src/llm_reviewer/telemetry/metrics.py`
- Modify: `pyproject.toml`
- Test: `tests/test_telemetry_config.py`
- Test: `tests/test_telemetry_metrics.py`

- [ ] Add typed config parsing for `[telemetry]`.
- [ ] Add model pricing parsing with `default` fallback.
- [ ] Add token usage parsing from Codex transcript text.
- [ ] Add a small OpenTelemetry wrapper with one metric attribute filter.
- [ ] Verify metric attributes reject high-cardinality fields.

### Task 2: Poller Integration And State

**Files:**
- Modify: `src/llm_reviewer/poller.py`
- Test: `tests/test_poller_telemetry_state.py`

- [ ] Add `review_runs` for review-level correlation.
- [ ] Extend `review_findings` with `run_id`, review classification fields, and `note_id`.
- [ ] Add `finding_outcomes` for GitLab discussion outcome snapshots.
- [ ] Record review start, completion, token usage, cost, and failure status.
- [ ] Emit review and finding metrics from existing poller flow.

### Task 3: Outcome Sync

**Files:**
- Modify: `src/llm_reviewer/poller.py`
- Create: `tests/test_outcome_sync.py`

- [ ] Add `--sync-outcomes`.
- [ ] Read posted findings with GitLab discussion IDs.
- [ ] Fetch discussion state from GitLab.
- [ ] Classify resolved, unresolved, deleted, developer-replied, disputed, duplicate, and false-positive markers.
- [ ] Emit outcome metrics and store latest snapshot in `finding_outcomes`.

### Task 4: Config And Documentation

**Files:**
- Modify: `config/env.toml`
- Modify: `README.md`
- Test: existing deploy/config tests.

- [ ] Document runtime metrics, after-the-fact metrics, and derived metrics.
- [ ] Document low-cardinality metric labels.
- [ ] Add disabled-by-default telemetry config with OTLP endpoint examples.
- [ ] Keep deployment generic and not tied to cron or a single host.

### Task 5: Verification

**Files:**
- All touched files.

- [ ] Run `uv sync --dev`.
- [ ] Run `uv run pytest -q`.
- [ ] Inspect secrets-sensitive output and ensure no tokens or env values are printed.
