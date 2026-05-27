# llm-reviewer

GitLab merge-request reviewer service.

The poller finds configured GitLab MRs, forks a review worker, runs the Codex
review skill, and posts inline GitLab review threads for findings that map to
changed lines.

## Layout

- `src/llm_reviewer/` - Python package and `uv` console entrypoints.
- `bin/` - deployment wrappers and shell helpers.
- `config/` - live config copied from the VM, including local secrets.
- `prompts/` - review meta prompt.
- `skills/` - Codex/Claude review skills.
- `plugins/` - bundled plugin assets needed by the VM install.
- `deploy/` - optional Codex and Claude config templates.
- `var/state/` - copied SQLite state only; runtime worktrees/logs are ignored.

## Local Checks

```sh
uv sync --dev
uv run pytest
```

## Review Limits

`config/poller.toml` controls queue and finding limits:

- `max_reviews_per_run`: maximum MRs queued per poll run.
- `max_findings_per_review`: maximum findings returned and posted/planned for a
  single MR review. Defaults to `5` when omitted.

The poller renders `prompts/00-meta.md` before each review and substitutes
`{{MAX_FINDINGS_PER_REVIEW}}` with `max_findings_per_review`. The Python
posting path also hard-caps parsed LLM findings to the same value.

## Telemetry

Telemetry uses OpenTelemetry. The app emits metrics, spans, and finding events;
the OTel backend or Collector owns rollups. `reviewer.sqlite` is only used for
review idempotency and GitLab discussion correlation.

Enable it in `config/poller.toml`:

```toml
[telemetry]
enabled = true
service_name = "llm-reviewer"
environment = "prod"
otlp_endpoint = "http://127.0.0.1:4317"
otlp_protocol = "grpc"
export_interval_seconds = 30

[telemetry.pricing.default]
input_per_1m = 0.0
output_per_1m = 0.0
cached_input_per_1m = 0.0
```

Runtime metrics are collected during each poller/worker run:

- eligible and reviewed MRs
- skipped MRs and skip reasons
- review duration and queue latency
- findings planned, posted, skipped, and pending external GitLab IDs
- no-finding reviews
- token usage and estimated cost
- Codex, GitLab, MCP, parser, and posting failures

Outcome metrics are collected after the fact with:

```sh
bin/mr-review-poller --sync-outcomes
```

That sync reads posted finding discussion IDs from `reviewer.sqlite`, checks
GitLab discussion state, and records resolved, unresolved-after-merge, deleted,
developer-replied, disputed, false-positive, and duplicate outcomes.

Derived dashboard metrics should be computed outside Python:

- cost per accepted actionable finding
- resolution rate
- useful finding rate
- false-positive rate
- cost per review
- cost per posted finding
- cost per blocking resolved finding
- failure rate
- monthly projected cost
- MR cycle-time impact

Metric attributes are intentionally low-cardinality:

```text
repo, model, prompt_version, review_mode, status, dry_run,
finding_type, severity, category, skip_reason, error_type,
component, operation, outcome, reviewer
```

MR IID, SHA, file path, line number, fingerprint, discussion ID, and note ID are
kept in SQLite or span events only. They are not metric labels.

## Package Deploy

From this checkout:

```sh
./scripts/deploy-package.sh user@host
```

That streams this project to the host, installs it under
`$HOME/.local/share/llm-reviewer`, runs `uv sync --locked --no-dev`, and
initializes the SQLite DB.

Choose a different root when needed:

```sh
./scripts/deploy-package.sh user@host --root /opt/llm-reviewer
```

Use `--sudo` only when the chosen root needs elevated filesystem writes:

```sh
./scripts/deploy-package.sh user@host --sudo --root /opt/llm-reviewer
```

Install Codex and Claude config templates for the target user when the host
should run reviews directly:

```sh
./scripts/deploy-package.sh user@host --install-agent-config
```

For a host-local install after copying the directory:

```sh
./scripts/install-package.sh
```

## Installed Runtime

The shell wrappers infer the install root from their own location, load
`config/*.env`, and execute the Python package through `uv run --project`.

Do not print or commit real values from `config/secrets.env`.
