# LLM Reviewer

[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![Managed with uv](https://img.shields.io/badge/managed%20with-uv-2f3542)](pyproject.toml)
[![CI](https://github.com/mountainowl/ai-code-review/actions/workflows/ci.yml/badge.svg)](https://github.com/mountainowl/ai-code-review/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/mountainowl/ai-code-review/badge)](https://scorecard.dev/viewer/?uri=github.com/mountainowl/ai-code-review)
[![SCM adapters](https://img.shields.io/badge/SCM-adapters-2f3542)](#project-status)
[![OpenTelemetry](https://img.shields.io/badge/metrics-OpenTelemetry-4f62ad)](#telemetry-and-roi)
[![License: MIT](https://img.shields.io/badge/license-MIT-111827)](LICENSE)

Evidence-backed LLM code review for merge requests.

![LLM Reviewer hero](docs/images/llm-reviewer-hero.png)

LLM Reviewer watches merge requests, runs a structured agent review, and posts
only actionable findings as inline review discussions. It is built for teams
that want early review signal without turning every MR into a chatbot thread.

## Demo

![LLM Reviewer demo](docs/media/llm-reviewer-demo.gif)

## Project Status

GitLab-first. Small by design. Production path: polling plus inline review
threads. Not primary yet: webhooks, GitHub posting, or pip-only installs without
checked-out assets.

Review execution is intentionally outside CI/CD. Run it as a poller beside your
existing pipelines.

## Why Teams Use It

- **Inline comments, not summaries.** Findings are mapped back to changed lines.
- **Evidence-first reviews.** The prompt requires impact, evidence, fix, and
  confidence before a finding is allowed through.
- **Human-friendly tone.** Short, direct comments. No praise, filler, broad
  audits, or style nits.
- **Stateful and idempotent.** SQLite records reviewed SHAs and posted
  fingerprints so the bot does not spam the same MR.
- **Ops-ready metrics.** OpenTelemetry reports reviews, findings, failures,
  tokens, estimated cost, and resolution outcomes.
- **Plain deployment.** A small Python package plus shell wrappers. No Docker
  requirement.
- **Fits beside existing CI/CD.** Start with polling and inline comments without
  rewriting pipelines.

## What It Does

```mermaid
flowchart TB
    A["Merge requests"]
    B["Poller and SQLite state"]
    C["Agent review skill"]
    D["Inline review discussions"]
    E["OpenTelemetry and outcome sync"]

    A --> B --> C --> D --> E
```

The poller does not try to be a code-review brain. It orchestrates SCM access,
state, prompt rendering, posting, and metrics. The actual review runs through
the configured CLI skill.

## Review Format

The bot comments in a predictable shape:

```text
Issue: HS256 JWT fallback is skipped when Cognito URL construction fails.
Impact: Valid local/shared-secret JWT requests return 500 instead of authenticating.
Evidence: The changed interceptor rethrows InvalidAwsUrlException before fallback runs.
Fix: Treat Cognito validation construction failures as failed Cognito auth when fallback is allowed.
Confidence: 0.94
```

Sanitized real inline finding:

![Example LLM Reviewer inline finding on a GitLab merge request](docs/images/gitlab-mr-review-example.png)

More sanitized examples are in [docs/examples](docs/examples/README.md).

## Prerequisites

- Python 3.14 or newer.
- `uv`.
- Git CLI.
- GitLab token with `api` scope.
- Codex CLI or Claude CLI configured on the review host.

## Quick Start

Install from a checkout so the prompt, skill, config template, wrapper scripts,
and deployment templates stay together:

```sh
git clone https://github.com/mountainowl/ai-code-review.git
cd ai-code-review
./scripts/install-package.sh
```

For local development:

```sh
git clone https://github.com/mountainowl/ai-code-review.git
cd ai-code-review
uv sync --dev
uv run pytest
```

Create local config:

```sh
cp config/env.example.toml config/env.toml
```

Add your GitLab and model credentials to the `[secrets]` table in
`config/env.toml`:

```toml
[secrets]
gitlab_token = "..."
openai_api_key = "..."
```

Run a one-off Codex review from the current checkout:

```sh
uv run code-review-codex "Review the current changes."
```

Run the GitLab poller:

```sh
uv run mr-review-poller
```

## GitLab Setup

1. Create a bot user such as `llm-reviewer`.
2. Add it to the target GitLab projects with permission to read MRs and create
   discussions.
3. Create a token with `api` scope.
4. Put the token in ignored `config/env.toml`.
5. Add projects to ignored `config/env.toml`.

Keep `dry_run = true` under `[review]` until the first review output looks right.

## Configuration

Public defaults live in `config/env.example.toml`. Copy it to ignored
`config/env.toml` before running. Runtime config and secrets live in that one
TOML file.

| Section | Setting | Default | Purpose / impact |
| --- | --- | --- | --- |
| `gitlab` | `url` | `https://gitlab.com` | Web host the poller reads MRs from. For self-hosted GitLab, keep `api_url` on the same host. |
| `gitlab` | `api_url` | `https://gitlab.com/api/v4` | API endpoint used by MCP tools inside the review agent. |
| `gitlab` | `bot_username` | `llm-reviewer` | Lets outcome sync separate bot comments from developer replies. |
| `gitlab` | `denied_tools_regex` | `^(delete_.*\|merge_merge_request\|push_files)$` | Blocks dangerous GitLab MCP tools even if the agent can see them. |
| `review` | `dry_run` | `true` | Stores planned findings without posting comments. Set `false` after test reviews look right. |
| `review` | `max_merge_requests_per_poll` | `8` | Caps how many MRs one poll cycle queues. Higher values can start more workers. |
| `review` | `max_findings_per_merge_request` | `8` | Caps findings per MR and fills `{{MAX_FINDINGS_PER_REVIEW}}` in the prompt. |
| `review` | `timeout_seconds` | `1800` | Kills a review worker that runs too long. |
| `poller` | `state_dir` | `var` | Stores SQLite state, logs, reports, worktrees, and rendered prompts. |
| `poller` | `interval_seconds` | `900` | Suggested wait for long-running poll loops. Cron/systemd can use another interval. |
| `poller` | `target_merge_request_iid` | unset | Temporary single-MR filter. Leave unset in production. |
| `agent` | `prompt_file` | `prompts/00-meta.md` | Meta prompt rendered before each review. |
| `agent` | `model` | `gpt-5.5` | Model passed to the review wrapper. Keep telemetry pricing aligned for cost metrics. |
| `agent` | `reasoning_effort` | `medium` | Review reasoning level. Higher values can cost more and run longer. |
| `agent` | `manual_review_dry_run` | `true` | Dry-run default for manual wrappers, separate from poller posting. |
| `agent` | `codex_profile` | `llm-reviewer` | Codex profile used by the Codex wrapper. |
| `agent` | `codex_sandbox` | `read-only` | Filesystem access passed to Codex review runs. |
| `secrets` | `gitlab_token` | unset | GitLab token with `api` scope. Exported as `GITLAB_TOKEN` and `GITLAB_PERSONAL_ACCESS_TOKEN`. |
| `secrets` | `openai_api_key` | unset | OpenAI key for Codex-backed reviews. Exported as `OPENAI_API_KEY`. |
| `secrets` | `anthropic_api_key` | unset | Anthropic key for Claude-backed reviews. Exported as `ANTHROPIC_API_KEY`. |
| `secrets` | `qwen_api_key` | unset | Optional Qwen key for custom wrappers. Exported as `QWEN_API_KEY`. |
| `telemetry` | `enabled` | `false` | Sends OTel metrics and spans when enabled. SQLite state is still written either way. |
| `telemetry` | `service_name` | `llm-reviewer` | Service name shown in the OTel backend. |
| `telemetry` | `environment` | `prod` | Environment label for dashboards, such as `dev`, `staging`, or `prod`. |
| `telemetry` | `otlp_endpoint` | `http://127.0.0.1:4317` | Collector endpoint for metrics and traces. |
| `telemetry` | `otlp_protocol` | `grpc` | OTLP transport. Only `grpc` is supported today. |
| `telemetry` | `export_interval_seconds` | `30` | Metric export interval. Lower values make dashboards fresher. |
| `telemetry` | `emit_finding_events` | `true` | Emits finding lifecycle metrics like planned, posted, skipped, and resolved. |
| `telemetry` | `emit_outcome_sync` | `true` | Emits metrics when outcome sync checks posted finding status. |
| `telemetry.pricing.default` | `input_per_1m` | `0.0` | Estimated input-token price per million tokens for cost metrics. |
| `telemetry.pricing.default` | `output_per_1m` | `0.0` | Estimated output-token price per million tokens for cost metrics. |
| `telemetry.pricing.default` | `cached_input_per_1m` | `0.0` | Estimated cached-input price per million tokens for cost metrics. |
| `projects` | `path` | sample repos | GitLab project path to poll, for example `group/repo`. |
| `projects` | `enabled` | `true` | Turns polling for that project on or off. |

## Deploy

Push the package to a host:

```sh
./scripts/deploy-package.sh user@host
```

That installs under `$HOME/.local/share/llm-reviewer`, runs
`uv sync --locked --no-dev`, and initializes SQLite state.

Common options:

```sh
# install to a custom root
./scripts/deploy-package.sh user@host --root /opt/llm-reviewer

# elevate when the root needs root-owned writes
./scripts/deploy-package.sh user@host --sudo --root /opt/llm-reviewer

# also install Codex and Claude config templates
./scripts/deploy-package.sh user@host --install-agent-config
```

For a host-local install after copying the checkout yourself:

```sh
./scripts/install-package.sh
```

The wrappers in `bin/` infer the install root from their own location and load
`config/env.toml`. No activation step is needed.

## Telemetry And ROI

LLM Reviewer emits OpenTelemetry metrics and spans so rollups stay outside the
poller. Useful dashboard slices:

- MRs reviewed, skipped, failed, and posted.
- Findings planned, posted, skipped, resolved, disputed, or deleted.
- Review latency, queue latency, and worker runtime.
- Input, output, cached, and total tokens.
- Estimated model cost per review, repo, finding, and resolved finding.
- Failure counts by component: GitLab, Codex, Claude, MCP, parser, posting.

Outcome sync checks posted GitLab discussions later:

```sh
bin/mr-review-poller --sync-outcomes
```

That records whether each finding was resolved, left unresolved after merge,
deleted, replied to, marked disputed, marked false-positive, or marked duplicate.

## Bot Avatar

Use `assets/llm-reviewer.png` as the GitLab or GitHub bot avatar.

![LLM Reviewer avatar preview](docs/images/llm-reviewer-avatar-preview.png)

## Community

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

## Safety

Do not print or commit real values from `config/env.toml`.

Ignored local files:

- `config/env.toml`
