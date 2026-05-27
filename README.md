# llm-reviewer

A small service that watches GitLab merge requests, runs an LLM code review
against them, and posts the findings back as inline review threads.

## Example

A real finding posted by the reviewer on a GitLab MR:

![Example LLM-Reviewer inline finding on a GitLab merge request](docs/images/gitlab-mr-review-example.png)

## How it works

1. A poller (`mr-review-poller`) wakes up on an interval, lists open MRs for
   each configured GitLab project, and decides which ones are ready to review.
2. For each eligible MR it forks a worker, which runs the Codex or Claude
   review skill against the diff.
3. The worker returns a list of findings. The poller maps each finding to a
   changed line and posts it as an inline GitLab discussion.
4. A SQLite file (`reviewer.sqlite`) records which MRs and findings have
   already been posted, so the same comment is never posted twice.

That's the whole loop. Everything else — telemetry, deploy scripts, config
files — exists to make that loop runnable on a server.

## Quick start (local)

```sh
uv sync --dev
uv run pytest
```

To run a one-off review against the current working directory using Codex:

```sh
uv run code-review-codex
```

To run the poller against your configured projects (respects `dry_run`):

```sh
uv run mr-review-poller
```

## Configuration

Configuration lives in three files under `config/`. Two are checked in; one
holds secrets and is not.

### `config/secrets.env` — tokens (not checked in)

Copy `config/secrets.env.example` to `config/secrets.env` and fill in real
values. Never commit this file.

| Variable         | Purpose                                           |
| ---------------- | ------------------------------------------------- |
| `GITLAB_TOKEN`   | Personal access token used to read MRs and post review threads. Needs `api` scope. |
| `OPENAI_API_KEY` | API key for the Codex review backend.             |

### `config/config.env` — runtime environment

Non-secret shell variables loaded by the `bin/` wrappers before each run.

| Variable                    | Default                                | Purpose |
| --------------------------- | -------------------------------------- | ------- |
| `LLM_CODE_REVIEW_ROOT`      | `$HOME/.local/share/llm-reviewer`      | Install root. The wrappers infer this from their own location if unset. |
| `LLM_CODE_REVIEW_BASE_DIR`  | `$LLM_CODE_REVIEW_ROOT/var`            | Where SQLite state, worktrees, and logs go. |
| `LLM_CODE_REVIEW_PROMPT`    | `$LLM_CODE_REVIEW_ROOT/prompts/00-meta.md` | Meta prompt rendered before each review. |
| `REVIEW_MODEL`              | `gpt-5.5`                              | Model name passed to the review backend. |
| `REVIEW_REASONING_EFFORT`   | `medium`                               | Effort hint (`low`, `medium`, `high`). |
| `REVIEW_DRY_RUN`            | `true`                                 | When `true`, the worker prints findings but the poller does not post. |
| `POLL_INTERVAL_SECONDS`     | `900`                                  | Seconds between poll cycles when run from a long-lived wrapper. |

### `config/poller.toml` — what to review and how much

This is the file you'll edit most often. It controls which projects are
reviewed, how many MRs are processed per cycle, and where telemetry goes.

```toml
gitlab_url             = "https://gitlab.com"
dry_run                = false   # if true, the poller logs would-post comments instead of posting
post_summary           = false   # post an overall summary comment in addition to inline threads
max_reviews_per_run    = 8       # cap MRs reviewed per poll cycle
max_findings_per_review = 8      # cap findings per MR (defaults to 5 if omitted)
review_timeout_seconds = 1800    # kill a worker that exceeds this

[[projects]]
path    = "group/repo"
enabled = true
```

The `max_findings_per_review` value is substituted into `prompts/00-meta.md`
(`{{MAX_FINDINGS_PER_REVIEW}}`) before each review, and is also enforced as a
hard cap in the posting path.

### `config/poller.toml` — telemetry

Telemetry is OpenTelemetry-only. The app emits metrics, spans, and finding
events; the OTel backend or Collector does the rollups. SQLite is **not** an
analytics store — it only holds posted-finding bookkeeping for idempotency.

```toml
[telemetry]
enabled                = true
service_name           = "llm-reviewer"
environment            = "prod"
otlp_endpoint          = "http://127.0.0.1:4317"
otlp_protocol          = "grpc"
export_interval_seconds = 30

[telemetry.pricing.default]
input_per_1m         = 0.0
output_per_1m        = 0.0
cached_input_per_1m  = 0.0
```

Each run emits, among other things:

- eligible vs. reviewed MR counts (and skip reasons)
- review duration and queue latency
- findings planned, posted, skipped, and pending external IDs
- token usage and estimated cost (using the pricing table above)
- failure counts per component (Codex, GitLab, MCP, parser, posting)

Higher-level metrics — useful-finding rate, cost per accepted finding,
resolution rate, monthly projected cost, MR cycle-time impact — are intentionally
**not** computed in Python. Derive them in your dashboard.

Attributes on metrics are kept low-cardinality on purpose:

```
repo, model, prompt_version, review_mode, status, dry_run,
finding_type, severity, category, skip_reason, error_type,
component, operation, outcome, reviewer
```

High-cardinality fields (MR IID, SHA, file path, line number, fingerprint,
discussion ID, note ID) live only in SQLite or span events, never as metric
labels.

## Outcome sync

After findings have been live for a while, you can grade them against what
actually happened in GitLab:

```sh
bin/mr-review-poller --sync-outcomes
```

That reads posted finding discussion IDs from `reviewer.sqlite`, checks each
discussion's state in GitLab, and records outcomes: resolved, unresolved-after-merge,
deleted, developer-replied, disputed, false-positive, duplicate.

Other CLI flags:

- `--init-db` — create `reviewer.sqlite` if missing, then exit.
- `--sync-limit N` — cap how many discussions `--sync-outcomes` checks per run (default 200).
- `--worker PATH` — run as a single-MR worker against a prepared worktree (used internally by the poller).

## Deploy

From this checkout, push the package to a host:

```sh
./scripts/deploy-package.sh user@host
```

That streams the project to the host, installs it under
`$HOME/.local/share/llm-reviewer`, runs `uv sync --locked --no-dev`, and
initializes the SQLite database.

Other deploy options:

```sh
# install to a custom root
./scripts/deploy-package.sh user@host --root /opt/llm-reviewer

# elevate when the root needs root-owned writes
./scripts/deploy-package.sh user@host --sudo --root /opt/llm-reviewer

# also drop Codex and Claude config templates into the target user's home
./scripts/deploy-package.sh user@host --install-agent-config
```

For a host-local install after copying the directory yourself:

```sh
./scripts/install-package.sh
```

## On the installed host

The wrappers in `bin/` infer the install root from their own location, source
`config/*.env`, and execute the Python package through `uv run --project`. So
on the host you can call `bin/mr-review-poller` directly — no extra activation
step.

**Do not print or commit real values from `config/secrets.env`.**
