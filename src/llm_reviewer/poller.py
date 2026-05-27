from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import tomllib

from llm_reviewer.env_config import apply_runtime_env, env_config_path, read_config_file
from llm_reviewer.telemetry import (
    ReviewTelemetry,
    TelemetryConfig,
    TokenUsage,
    estimate_cost_usd,
    parse_codex_token_usage,
    telemetry_config_from_dict,
)

ROOT = Path(os.environ.get("LLM_CODE_REVIEW_ROOT", Path.home() / ".local" / "share" / "llm-reviewer"))
CONFIG = env_config_path(ROOT)
DB = ROOT / "var" / "state" / "reviewer.sqlite"
WORK = ROOT / "var" / "work"
REPORTS = ROOT / "var" / "reports"
JOBS = ROOT / "var" / "jobs"
LOGS = ROOT / "var" / "log"
RENDERED_PROMPTS = ROOT / "var" / "rendered-prompts"
DEFAULT_REVIEWER = ROOT / "bin" / "code-review-codex"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(event: str, **fields) -> None:
    print(json.dumps({"ts": now(), "event": event, **fields}, sort_keys=True), flush=True)


def gitlab_token() -> str:
    for key in ("GITLAB_TOKEN", "GITLAB_PERSONAL_ACCESS_TOKEN", "GLAB_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    raise SystemExit("missing GitLab token")


def read_config() -> dict:
    if not CONFIG.exists():
        raise SystemExit(f"missing config: {CONFIG}")
    cfg = read_config_file(CONFIG)
    apply_runtime_env(ROOT, cfg)
    cfg.setdefault("gitlab_url", "https://gitlab.com")
    cfg.setdefault("dry_run", True)
    cfg.setdefault("post_summary", False)
    cfg.setdefault("max_reviews_per_run", 5)
    cfg.setdefault("max_findings_per_review", 5)
    cfg.setdefault("review_timeout_seconds", 1800)
    cfg.setdefault("reviewer_command", [str(DEFAULT_REVIEWER)])
    cfg.setdefault("target_mr_iid", None)
    cfg["max_findings_per_review"] = positive_int(cfg["max_findings_per_review"], "max_findings_per_review")
    try:
        cfg["telemetry_config"] = telemetry_config_from_dict(cfg)
    except (TypeError, ValueError) as exc:
        cfg["telemetry_config"] = TelemetryConfig(enabled=False)
        log("telemetry_config_disabled", error=str(exc))
    cfg["projects"] = [
        item["path"]
        for item in cfg.get("projects", []) + cfg.get("project", [])
        if item.get("enabled", True) and item.get("path")
    ]
    return cfg


def positive_int(value: object, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"{name} must be a positive integer") from None
    if parsed < 1:
        raise SystemExit(f"{name} must be a positive integer")
    return parsed


def init_dirs() -> None:
    for path in (DB.parent, WORK, REPORTS, JOBS, LOGS, RENDERED_PROMPTS):
        path.mkdir(parents=True, exist_ok=True)


def connect_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB, timeout=30)
    db.execute("pragma journal_mode=WAL")
    db.execute("pragma busy_timeout=5000")
    return db


def init_db() -> None:
    init_dirs()
    with connect_db() as db:
        db.execute(
            """
            create table if not exists reviewed_mrs (
              project text not null,
              iid integer not null,
              sha text not null,
              status text not null,
              report text,
              error text,
              updated_at text not null,
              primary key(project, iid, sha)
            )
            """
        )
        db.execute(
            """
            create table if not exists review_runs (
              run_id text primary key,
              project text not null,
              iid integer not null,
              sha text not null,
              status text not null,
              model text,
              prompt_version text,
              review_mode text,
              dry_run integer not null,
              started_at text not null,
              finished_at text,
              tokens_input integer,
              tokens_output integer,
              tokens_cached integer,
              tokens_total integer,
              cost_usd real,
              error text
            )
            """
        )
        db.execute(
            """
            create table if not exists review_findings (
              project text not null,
              iid integer not null,
              sha text not null,
              fingerprint text not null,
              file text not null,
              line integer,
              status text not null,
              discussion_id text,
              body text not null,
              updated_at text not null,
              primary key(project, iid, sha, fingerprint)
            )
            """
        )
        for name, definition in {
            "run_id": "text",
            "type": "text",
            "severity": "text",
            "category": "text",
            "confidence": "real",
            "note_id": "text",
        }.items():
            ensure_column(db, "review_findings", name, definition)
        db.execute(
            """
            create table if not exists finding_outcomes (
              finding_id text primary key,
              project text not null,
              iid integer not null,
              sha text not null,
              fingerprint text not null,
              discussion_id text,
              resolved integer not null default 0,
              deleted integer not null default 0,
              developer_replied integer not null default 0,
              disputed integer not null default 0,
              false_positive integer not null default 0,
              duplicate integer not null default 0,
              resolved_at text,
              merged_unresolved integer not null default 0,
              last_checked_at text not null
            )
            """
        )


def ensure_column(db: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    columns = {row[1] for row in db.execute(f"pragma table_info({table})").fetchall()}
    if name not in columns:
        db.execute(f"alter table {table} add column {name} {definition}")


def review_run_id(project: str, iid: int, sha: str) -> str:
    payload = json.dumps({"project": project, "iid": iid, "sha": sha}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def prompt_version(prompt: Path) -> str:
    try:
        return hashlib.sha256(prompt.read_bytes()).hexdigest()[:12]
    except OSError:
        return "unknown"


def reviewer_model(cfg: dict) -> str:
    if cfg.get("model"):
        return str(cfg["model"])
    command = cfg.get("reviewer_command") or []
    if command:
        return Path(str(command[0])).name
    return "unknown"


def record_review_run_start(
    *,
    run_id: str,
    project: str,
    iid: int,
    sha: str,
    model: str,
    prompt_version: str,
    review_mode: str,
    dry_run: bool,
) -> None:
    with connect_db() as db:
        db.execute(
            """
            insert into review_runs(run_id,project,iid,sha,status,model,prompt_version,review_mode,dry_run,started_at)
            values(?,?,?,?,?,?,?,?,?,?)
            on conflict(run_id) do update set
              status=excluded.status,
              model=excluded.model,
              prompt_version=excluded.prompt_version,
              review_mode=excluded.review_mode,
              dry_run=excluded.dry_run,
              started_at=excluded.started_at,
              finished_at=null,
              error=null
            """,
            (run_id, project, iid, sha, "running", model, prompt_version, review_mode, int(dry_run), now()),
        )


def record_review_run_finish(
    *,
    run_id: str,
    status: str,
    tokens: TokenUsage,
    cost_usd: float,
    error: str | None,
) -> None:
    with connect_db() as db:
        db.execute(
            """
            update review_runs set
              status=?,
              finished_at=?,
              tokens_input=?,
              tokens_output=?,
              tokens_cached=?,
              tokens_total=?,
              cost_usd=?,
              error=?
            where run_id=?
            """,
            (
                status,
                now(),
                tokens.input,
                tokens.output,
                tokens.cached,
                tokens.total,
                cost_usd,
                error,
                run_id,
            ),
        )


def record(project: str, iid: int, sha: str, status: str, report: str | None = None, error: str | None = None) -> None:
    with connect_db() as db:
        db.execute(
            """
            insert into reviewed_mrs(project,iid,sha,status,report,error,updated_at)
            values(?,?,?,?,?,?,?)
            on conflict(project,iid,sha) do update set
              status=excluded.status,
              report=excluded.report,
              error=excluded.error,
              updated_at=excluded.updated_at
            """,
            (project, iid, sha, status, report, error, now()),
        )


def already_seen(project: str, iid: int, sha: str) -> bool:
    with connect_db() as db:
        row = db.execute(
            """
            select 1 from reviewed_mrs
            where project=? and iid=? and sha=?
              and status in ('queued','running','success','no_findings')
            """,
            (project, iid, sha),
        ).fetchone()
    return row is not None


def api(base: str, token: str, method: str, path: str, body: dict | None = None):
    payload = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/api/v4" + path,
        data=payload,
        method=method,
        headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read().decode() or "null"
        headers = dict(resp.headers)
    return json.loads(data), headers


def api_pages(base: str, token: str, path: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    sep = "&" if "?" in path else "?"
    while True:
        data, headers = api(base, token, "GET", f"{path}{sep}per_page=100&page={page}")
        out.extend(data)
        next_page = headers.get("X-Next-Page") or headers.get("x-next-page")
        if not next_page:
            return out
        page = int(next_page)


def open_mrs(cfg: dict, project: str, token: str) -> list[dict]:
    encoded = urllib.parse.quote(project, safe="")
    out = []
    page = 1
    while True:
        qs = urllib.parse.urlencode({"state": "opened", "scope": "all", "per_page": 100, "page": page})
        data, headers = api(cfg["gitlab_url"], token, "GET", f"/projects/{encoded}/merge_requests?{qs}")
        out.extend(data)
        next_page = headers.get("X-Next-Page") or headers.get("x-next-page")
        if not next_page:
            return out
        page = int(next_page)


def get_mr(cfg: dict, token: str, project: str, iid: int) -> dict:
    encoded = urllib.parse.quote(project, safe="")
    data, _ = api(cfg["gitlab_url"], token, "GET", f"/projects/{encoded}/merge_requests/{iid}")
    return data


def get_mr_diffs(cfg: dict, token: str, project: str, iid: int) -> list[dict]:
    encoded = urllib.parse.quote(project, safe="")
    return api_pages(cfg["gitlab_url"], token, f"/projects/{encoded}/merge_requests/{iid}/diffs")


def get_mr_discussion(cfg: dict, token: str, project: str, iid: int, discussion_id: str) -> dict:
    encoded = urllib.parse.quote(project, safe="")
    data, _ = api(
        cfg["gitlab_url"],
        token,
        "GET",
        f"/projects/{encoded}/merge_requests/{iid}/discussions/{urllib.parse.quote(discussion_id, safe='')}",
    )
    return data


def slug(value: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in value).strip("-").lower()


def run(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = 600,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def extract_findings(raw: str, max_findings: int | None = None) -> list[dict]:
    text = raw.strip()
    if not text or text == "No actionable findings.":
        return []
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        candidates = []
        for match in re.finditer(r"\[", text):
            try:
                candidate, _ = decoder.raw_decode(text[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, list) and all(isinstance(item, dict) for item in candidate):
                candidates.append(candidate)
        if not candidates:
            raise ValueError("review output is not JSON findings")
        data = candidates[-1]
    if isinstance(data, dict):
        data = data.get("findings", [])
    if not isinstance(data, list):
        raise ValueError("review JSON must be an array or an object with findings")
    findings = [item for item in data if isinstance(item, dict)]
    if max_findings is not None:
        return findings[:positive_int(max_findings, "max_findings")]
    return findings


def render_meta_prompt(prompt_text: str, max_findings: int) -> str:
    limit = positive_int(max_findings, "max_findings_per_review")
    return prompt_text.replace("{{MAX_FINDINGS_PER_REVIEW}}", str(limit))


def write_rendered_meta_prompt(cfg: dict) -> Path:
    source = Path(os.environ.get("LLM_CODE_REVIEW_PROMPT_SOURCE", ROOT / "prompts" / "00-meta.md"))
    if not source.is_file():
        raise RuntimeError(f"meta prompt is not readable: {source}")
    limit = positive_int(cfg.get("max_findings_per_review", 5), "max_findings_per_review")
    rendered = render_meta_prompt(source.read_text(encoding="utf-8"), limit)
    RENDERED_PROMPTS.mkdir(parents=True, exist_ok=True)
    target = RENDERED_PROMPTS / f"00-meta.max-{limit}.md"
    if not target.exists() or target.read_text(encoding="utf-8") != rendered:
        target.write_text(rendered, encoding="utf-8")
    return target


def changed_lines_from_diffs(diffs: list[dict]) -> dict[str, dict]:
    changed: dict[str, dict] = {}
    hunk = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for item in diffs:
        new_path = item.get("new_path") or item.get("newPath")
        old_path = item.get("old_path") or item.get("oldPath") or new_path
        diff_text = item.get("diff") or ""
        if not new_path:
            continue
        entry = changed.setdefault(
            new_path,
            {"new_path": new_path, "old_path": old_path, "new_lines": set()},
        )
        new_line = None
        for line in diff_text.splitlines():
            match = hunk.match(line)
            if match:
                new_line = int(match.group(1))
                continue
            if new_line is None or line.startswith("\\"):
                continue
            if line.startswith("+") and not line.startswith("+++"):
                entry["new_lines"].add(new_line)
                new_line += 1
            elif line.startswith("-") and not line.startswith("---"):
                continue
            else:
                new_line += 1
    return changed


def build_position(mr: dict, changed: dict[str, dict], finding: dict) -> dict | None:
    file_path = finding.get("file") or finding.get("path")
    line = finding.get("line") or finding.get("new_line")
    if not file_path or line is None:
        return None
    try:
        line = int(line)
    except (TypeError, ValueError):
        return None
    entry = changed.get(file_path)
    if not entry or line not in entry["new_lines"]:
        return None
    refs = mr.get("diff_refs") or {}
    if not refs.get("base_sha") or not refs.get("start_sha") or not refs.get("head_sha"):
        return None
    return {
        "position_type": "text",
        "base_sha": refs["base_sha"],
        "start_sha": refs["start_sha"],
        "head_sha": refs["head_sha"],
        "old_path": entry["old_path"],
        "new_path": entry["new_path"],
        "new_line": line,
    }


def finding_body(finding: dict) -> str:
    body = finding.get("body") or finding.get("comment")
    title = finding.get("title") or "review finding"
    kind = finding.get("type") or "issue"
    severity = finding.get("severity") or "blocking"
    category = finding.get("category") or "correctness"
    impact = finding.get("impact")
    evidence = finding.get("evidence")
    fix = finding.get("fix")
    confidence = finding.get("confidence")
    parts = [f"**{kind.title()} ({severity}, {category}):** {str(title).strip()}"]
    if impact:
        parts.append(f"**Impact:** {impact}")
    if evidence:
        parts.append(f"**Evidence:** {evidence}")
    if fix:
        parts.append(f"**Fix:** {fix}")
    if confidence is not None:
        parts.append(f"**Confidence:** {confidence}")
    if body and len(parts) == 1:
        parts.append(str(body).strip())
    return "\n\n".join(parts).strip()


def finding_fingerprint(project: str, iid: int, sha: str, finding: dict) -> str:
    payload = {
        "project": project,
        "iid": iid,
        "sha": sha,
        "file": finding.get("file") or finding.get("path"),
        "line": finding.get("line") or finding.get("new_line"),
        "body": " ".join(finding_body(finding).split()),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def finding_seen(project: str, iid: int, sha: str, fingerprint: str) -> bool:
    with connect_db() as db:
        row = db.execute(
            """
            select 1 from review_findings
            where project=? and iid=? and sha=? and fingerprint=?
              and status = 'posted'
            """,
            (project, iid, sha, fingerprint),
        ).fetchone()
    return row is not None


def record_finding(
    project: str,
    iid: int,
    sha: str,
    fingerprint: str,
    finding: dict,
    status: str,
    discussion_id: str | None = None,
    run_id: str | None = None,
    note_id: str | None = None,
) -> None:
    file_path = str(finding.get("file") or finding.get("path") or "")
    line = finding.get("line") or finding.get("new_line")
    line = int(line) if line is not None else None
    confidence = finding.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    with connect_db() as db:
        db.execute(
            """
            insert into review_findings(
              project,iid,sha,fingerprint,file,line,status,discussion_id,body,updated_at,
              run_id,type,severity,category,confidence,note_id
            )
            values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            on conflict(project,iid,sha,fingerprint) do update set
              status=excluded.status,
              discussion_id=excluded.discussion_id,
              body=excluded.body,
              run_id=excluded.run_id,
              type=excluded.type,
              severity=excluded.severity,
              category=excluded.category,
              confidence=excluded.confidence,
              note_id=excluded.note_id,
              updated_at=excluded.updated_at
            """,
            (
                project,
                iid,
                sha,
                fingerprint,
                file_path,
                line,
                status,
                discussion_id,
                finding_body(finding),
                now(),
                run_id,
                finding.get("type"),
                finding.get("severity"),
                finding.get("category"),
                confidence,
                note_id,
            ),
        )


def mcp_thread_args(project: str, iid: int, body: str, position: dict) -> dict:
    return {
        "project_id": urllib.parse.quote(project, safe=""),
        "merge_request_iid": str(iid),
        "body": body,
        "position": position,
    }


def mcp_call_tool(name: str, arguments: dict) -> dict:
    proc = subprocess.Popen(
        [str(ROOT / "bin" / "mcp-gitlab")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "llm-code-review-poller", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    ]
    for message in messages:
        proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()

    try:
        for line in proc.stdout:
            if not line.strip():
                continue
            response = json.loads(line)
            if response.get("id") != 2:
                continue
            if response.get("error"):
                raise RuntimeError(json.dumps(response["error"]))
            return response.get("result") or {}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    raise RuntimeError(f"MCP tool did not return: {name}")


def mcp_discussion_id(result: dict) -> str:
    for item in result.get("content") or []:
        text = item.get("text") if isinstance(item, dict) else None
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"])
    return ""


def classify_discussion_outcome(discussion: dict, bot_username: str, mr_state: str) -> dict:
    notes = discussion.get("notes") or []
    resolved = bool(discussion.get("resolved", False))
    active_notes = [note for note in notes if not note.get("deleted")]
    developer_replied = any(
        ((note.get("author") or {}).get("username") or "") != bot_username
        for note in active_notes
    )
    note_text = "\n".join(str(note.get("body") or "").lower() for note in active_notes)
    false_positive = "[llm-review:false-positive]" in note_text
    duplicate = "[llm-review:duplicate]" in note_text
    disputed = "[llm-review:disputed]" in note_text or false_positive
    return {
        "resolved": resolved,
        "deleted": bool(discussion.get("deleted", False)) or (bool(notes) and not active_notes),
        "developer_replied": developer_replied,
        "disputed": disputed,
        "false_positive": false_positive,
        "duplicate": duplicate,
        "resolved_at": discussion.get("resolved_at"),
        "merged_unresolved": mr_state == "merged" and not resolved,
    }


def record_finding_outcome(
    *,
    project: str,
    iid: int,
    sha: str,
    fingerprint: str,
    discussion_id: str,
    outcome: dict,
) -> None:
    finding_id = f"{project}:{iid}:{sha}:{fingerprint}"
    with connect_db() as db:
        db.execute(
            """
            insert into finding_outcomes(
              finding_id,project,iid,sha,fingerprint,discussion_id,
              resolved,deleted,developer_replied,disputed,false_positive,duplicate,
              resolved_at,merged_unresolved,last_checked_at
            )
            values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            on conflict(finding_id) do update set
              discussion_id=excluded.discussion_id,
              resolved=excluded.resolved,
              deleted=excluded.deleted,
              developer_replied=excluded.developer_replied,
              disputed=excluded.disputed,
              false_positive=excluded.false_positive,
              duplicate=excluded.duplicate,
              resolved_at=excluded.resolved_at,
              merged_unresolved=excluded.merged_unresolved,
              last_checked_at=excluded.last_checked_at
            """,
            (
                finding_id,
                project,
                iid,
                sha,
                fingerprint,
                discussion_id,
                int(bool(outcome["resolved"])),
                int(bool(outcome["deleted"])),
                int(bool(outcome["developer_replied"])),
                int(bool(outcome["disputed"])),
                int(bool(outcome["false_positive"])),
                int(bool(outcome["duplicate"])),
                outcome.get("resolved_at"),
                int(bool(outcome["merged_unresolved"])),
                now(),
            ),
        )


def posted_findings_for_outcome_sync(limit: int = 200) -> list[dict]:
    with connect_db() as db:
        rows = db.execute(
            """
            select rf.project,rf.iid,rf.sha,rf.fingerprint,rf.discussion_id
            from review_findings rf
            left join finding_outcomes fo
              on fo.finding_id = rf.project || ':' || rf.iid || ':' || rf.sha || ':' || rf.fingerprint
            where rf.status='posted' and rf.discussion_id is not null and rf.discussion_id != ''
            order by
              case when fo.last_checked_at is null then 0 else 1 end,
              fo.last_checked_at asc,
              rf.updated_at asc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "project": row[0],
            "iid": int(row[1]),
            "sha": row[2],
            "fingerprint": row[3],
            "discussion_id": row[4],
        }
        for row in rows
    ]


def sha_for(mr: dict) -> str:
    return mr.get("sha") or mr.get("diff_refs", {}).get("head_sha") or ""


def write_job(project: str, mr: dict) -> Path:
    iid = int(mr["iid"])
    sha = sha_for(mr)
    path = JOBS / f"{slug(project)}-{iid}-{sha[:12]}.json"
    path.write_text(json.dumps({"project": project, "mr": mr}, indent=2), encoding="utf-8")
    return path


def fork_worker(job: Path) -> int:
    log_file = LOGS / f"{job.stem}.log"
    out = open(log_file, "ab", buffering=0)
    configured = os.environ.get("LLM_REVIEWER_WORKER_COMMAND")
    command = shlex.split(configured) if configured else [sys.executable, "-m", "llm_reviewer.poller"]
    proc = subprocess.Popen(
        command + ["--worker", str(job)],
        stdout=out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log("worker_forked", pid=proc.pid, job=str(job), log=str(log_file))
    return proc.pid


def poll() -> int:
    init_db()
    cfg = read_config()
    token = gitlab_token()
    queued = 0
    target_mr_iid = cfg.get("target_mr_iid")
    for project in cfg["projects"]:
        log("poll_project", project=project)
        for mr in open_mrs(cfg, project, token):
            iid = int(mr["iid"])
            if target_mr_iid is not None and iid != int(target_mr_iid):
                continue
            sha = sha_for(mr)
            if not sha or already_seen(project, iid, sha):
                continue
            record(project, iid, sha, "queued")
            job = write_job(project, mr)
            fork_worker(job)
            queued += 1
            if queued >= int(cfg["max_reviews_per_run"]):
                return queued
    if queued == 0:
        log("no_pending_reviews")
    return queued


def checkout(project: str, mr: dict) -> Path:
    iid = int(mr["iid"])
    sha = sha_for(mr)
    repo = WORK / slug(project) / str(iid) / sha[:12]
    repo.parent.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        result = run(["glab", "repo", "clone", project, str(repo)], timeout=900)
        if result.returncode:
            raise RuntimeError(result.stdout[-3000:])
    for args in (
        ["git", "fetch", "origin", "--prune"],
        ["git", "fetch", "origin", f"refs/merge-requests/{iid}/head:refs/remotes/origin/mr-{iid}"],
        ["git", "checkout", "--detach", sha],
    ):
        result = run(args, cwd=repo, timeout=900)
        if result.returncode:
            raise RuntimeError(result.stdout[-3000:])
    return repo


def review_prompt(project: str, mr: dict, cfg: dict | None = None) -> str:
    max_findings = positive_int((cfg or {}).get("max_findings_per_review", 5), "max_findings_per_review")
    return f"""Review GitLab MR {mr.get("web_url")}
Project: {project}
MR IID: {mr.get("iid")}
Title: {mr.get("title")}
source branch: {mr.get("source_branch")}
target branch: {mr.get("target_branch")}
head SHA: {sha_for(mr)}

Use the `code-review` skill through Superpowers for the review contract.
Review the MR diff only. Do not post comments to GitLab.
Return a JSON array only. Do not wrap it in markdown.
Return at most {max_findings} findings.
Each finding object must have:
- type
- severity
- category
- title
- file
- line
- impact
- evidence
- fix
- confidence
type must be one of: issue, suggestion, question.
severity must be one of: blocking, non-blocking.
confidence must be a number from 0 to 1.
Use line for the changed new-line where the inline comment should be placed.
Return [] when there are no actionable findings."""


def post_inline_finding(cfg: dict, token: str, project: str, iid: int, body: str, position: dict) -> str:
    result = mcp_call_tool("create_merge_request_thread", mcp_thread_args(project, iid, body, position))
    return mcp_discussion_id(result)


def emit_finding_metric(
    telemetry: ReviewTelemetry | None,
    *,
    repo: str,
    status: str,
    finding: dict,
    dry_run: bool,
) -> None:
    if telemetry and telemetry.config.emit_finding_events:
        telemetry.record_finding(repo=repo, status=status, finding=finding, dry_run=dry_run)


def post_or_plan_findings(
    cfg: dict,
    token: str,
    project: str,
    mr: dict,
    raw_review: str,
    *,
    run_id: str | None = None,
    telemetry: ReviewTelemetry | None = None,
) -> tuple[int, int, int]:
    iid = int(mr["iid"])
    sha = sha_for(mr)
    findings = extract_findings(raw_review, max_findings=cfg.get("max_findings_per_review", 5))
    if not findings:
        return (0, 0, 0)
    mr = get_mr(cfg, token, project, iid)
    diffs = get_mr_diffs(cfg, token, project, iid)
    changed = changed_lines_from_diffs(diffs)
    posted = planned = skipped = 0
    for finding in findings:
        fp = finding_fingerprint(project, iid, sha, finding)
        if finding_seen(project, iid, sha, fp):
            skipped += 1
            continue
        position = build_position(mr, changed, finding)
        if not position:
            record_finding(project, iid, sha, fp, finding, "skipped", run_id=run_id)
            emit_finding_metric(
                telemetry,
                repo=project,
                status="skipped",
                finding=finding,
                dry_run=cfg["dry_run"],
            )
            log(
                "finding_skipped",
                project=project,
                iid=iid,
                file=finding.get("file") or finding.get("path"),
                line=finding.get("line") or finding.get("new_line"),
                reason="line_not_in_diff",
            )
            skipped += 1
            continue
        body = finding_body(finding)
        if cfg["dry_run"]:
            record_finding(project, iid, sha, fp, finding, "planned", run_id=run_id)
            emit_finding_metric(telemetry, repo=project, status="planned", finding=finding, dry_run=True)
            log("finding_planned", project=project, iid=iid, file=position["new_path"], line=position["new_line"])
            planned += 1
        else:
            discussion_id = post_inline_finding(cfg, token, project, iid, body, position)
            if not discussion_id:
                record_finding(project, iid, sha, fp, finding, "pending_external_id", run_id=run_id)
                emit_finding_metric(
                    telemetry,
                    repo=project,
                    status="pending_external_id",
                    finding=finding,
                    dry_run=False,
                )
                log(
                    "finding_pending_external_id",
                    project=project,
                    iid=iid,
                    file=position["new_path"],
                    line=position["new_line"],
                )
                skipped += 1
                continue
            record_finding(project, iid, sha, fp, finding, "posted", discussion_id, run_id=run_id)
            emit_finding_metric(telemetry, repo=project, status="posted", finding=finding, dry_run=False)
            log(
                "finding_posted",
                project=project,
                iid=iid,
                file=position["new_path"],
                line=position["new_line"],
                discussion_id=discussion_id,
            )
            posted += 1
    return (posted, planned, skipped)


def worker(job: Path) -> int:
    init_db()
    cfg = read_config()
    token = gitlab_token()
    telemetry = ReviewTelemetry.from_config(cfg["telemetry_config"])
    data = json.loads(job.read_text())
    project = data["project"]
    mr = data["mr"]
    iid = int(mr["iid"])
    sha = sha_for(mr)
    run_id = review_run_id(project, iid, sha)
    model = reviewer_model(cfg)
    report = REPORTS / slug(project) / str(iid) / sha[:12] / "review.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    log("review_start", project=project, iid=iid, sha=sha, reviewer=cfg["reviewer_command"], dry_run=cfg["dry_run"])
    started = time.monotonic()
    record(project, iid, sha, "running", str(report))
    tokens = TokenUsage()
    cost_usd = 0.0
    try:
        rendered_prompt = write_rendered_meta_prompt(cfg)
        record_review_run_start(
            run_id=run_id,
            project=project,
            iid=iid,
            sha=sha,
            model=model,
            prompt_version=prompt_version(rendered_prompt),
            review_mode="diff",
            dry_run=bool(cfg["dry_run"]),
        )
        with telemetry.span("llm_review.run", repo=project, mr_iid=iid, sha=sha, run_id=run_id):
            repo = checkout(project, mr)
            env = os.environ.copy()
            env["LLM_CODE_REVIEW_PROMPT"] = str(rendered_prompt)
            env["LLM_REVIEW_MAX_FINDINGS"] = str(cfg["max_findings_per_review"])
            result = run(
                cfg["reviewer_command"] + [review_prompt(project, mr, cfg)],
                cwd=repo,
                timeout=int(cfg["review_timeout_seconds"]),
                env=env,
            )
            report.write_text(result.stdout, encoding="utf-8")
            tokens = parse_codex_token_usage(result.stdout)
            cost_usd = estimate_cost_usd(tokens, cfg["telemetry_config"].price_for(model))
            if result.returncode:
                raise RuntimeError(f"review exited {result.returncode}")
            posted, planned, skipped = post_or_plan_findings(
                cfg,
                token,
                project,
                mr,
                result.stdout,
                run_id=run_id,
                telemetry=telemetry,
            )
            status = "no_findings" if (posted, planned, skipped) == (0, 0, 0) else "success"
            record(project, iid, sha, status, str(report))
            record_review_run_finish(
                run_id=run_id,
                status=status,
                tokens=tokens,
                cost_usd=cost_usd,
                error=None,
            )
            telemetry.record_review_done(
                repo=project,
                model=model,
                status=status,
                review_mode="diff",
                dry_run=bool(cfg["dry_run"]),
                duration_seconds=round(time.monotonic() - started, 2),
                tokens=tokens,
                cost_usd=cost_usd,
            )
            log(
                "review_done",
                project=project,
                iid=iid,
                sha=sha,
                status=status,
                posted=posted,
                planned=planned,
                skipped=skipped,
                seconds=round(time.monotonic() - started, 2),
                tokens_total=tokens.total,
                cost_usd=cost_usd,
                report=str(report),
            )
            return 0
    except Exception as exc:
        report.write_text(str(exc), encoding="utf-8")
        record(project, iid, sha, "failed", str(report), str(exc))
        record_review_run_finish(
            run_id=run_id,
            status="failed",
            tokens=tokens,
            cost_usd=cost_usd,
            error=str(exc),
        )
        telemetry.record_failure(repo=project, error_type=type(exc).__name__, operation="review")
        telemetry.record_review_done(
            repo=project,
            model=model,
            status="failed",
            review_mode="diff",
            dry_run=bool(cfg.get("dry_run", True)),
            duration_seconds=round(time.monotonic() - started, 2),
            tokens=tokens,
            cost_usd=cost_usd,
        )
        log("review_failed", project=project, iid=iid, sha=sha, error=str(exc), report=str(report))
        return 1


def sync_outcomes(limit: int = 200) -> int:
    init_db()
    cfg = read_config()
    token = gitlab_token()
    telemetry = ReviewTelemetry.from_config(cfg["telemetry_config"])
    bot_username = os.environ.get("LLM_REVIEWER_GITLAB_USERNAME", "llm-reviewer")
    synced = 0
    for finding in posted_findings_for_outcome_sync(limit):
        project = finding["project"]
        iid = int(finding["iid"])
        try:
            mr = get_mr(cfg, token, project, iid)
            discussion = get_mr_discussion(cfg, token, project, iid, finding["discussion_id"])
            outcome = classify_discussion_outcome(
                discussion,
                bot_username=bot_username,
                mr_state=str(mr.get("state") or ""),
            )
            record_finding_outcome(
                project=project,
                iid=iid,
                sha=finding["sha"],
                fingerprint=finding["fingerprint"],
                discussion_id=finding["discussion_id"],
                outcome=outcome,
            )
            for name in ("resolved", "deleted", "developer_replied", "disputed", "false_positive", "duplicate"):
                if outcome[name] and telemetry.config.emit_outcome_sync:
                    telemetry.record_finding(
                        repo=project,
                        status=name,
                        finding={"type": "unknown", "severity": "unknown", "category": "unknown"},
                        dry_run=False,
                    )
            synced += 1
        except Exception as exc:
            telemetry.record_failure(repo=project, error_type=type(exc).__name__, operation="outcome_sync")
            log("outcome_sync_failed", project=project, iid=iid, error=str(exc))
    log("outcome_sync_done", synced=synced)
    return synced


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--sync-outcomes", action="store_true")
    parser.add_argument("--sync-limit", type=int, default=200)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.init_db:
        init_db()
        log("db_ready", path=str(DB))
        return 0
    if args.sync_outcomes:
        sync_outcomes(args.sync_limit)
        return 0
    if args.worker:
        return worker(args.worker)
    poll()
    return 0


if __name__ == "__main__":
    sys.exit(main())
