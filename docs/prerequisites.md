# Prerequisites

A short list of tools to install on the review host before Bubo runs.
Nothing is bundled implicitly — a missing tool fails its code path right away.

## Runtime — required for any review

Needed for every review path, whatever provider you use. Copy-paste a block:

**macOS**

```sh
# uv — project + dependency manager. Every CLI script invokes `uv run`.
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.14+ — runtime. Managed by uv so the project version stays pinned.
uv python install 3.14

# Git CLI — the worker runs `git fetch` / `git checkout` against change refs.
brew install git

# Codex CLI or Claude CLI — the configured review agent. Install one and
# authenticate to your LLM provider:
#   Codex:       https://github.com/openai/codex
#   Claude Code: https://www.anthropic.com/claude-code

# Superpowers + `code-reviewer` skill — the review prompt invokes
# /using-superpowers and the $code-reviewer skill. Without Superpowers
# configured in your CLI the agent will not run the review contract.
# Install Superpowers into your Codex/Claude config. The bundled skill
# assets live under plugins/superpowers/ and skills/code-reviewer/.
#   https://github.com/obra/superpowers
```

**Linux (Debian/Ubuntu)**

```sh
# uv — same one-liner across platforms.
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.14+ — uv manages it; distro package is a fallback.
uv python install 3.14

# Git CLI — distro package.
sudo apt install -y git

# Codex CLI / Claude CLI / Superpowers — see the macOS block; install steps
# are platform-agnostic (npm / shell installer / config file).
```

## Per-provider

There are **no extra CLIs to install** for either provider. Checkout uses plain
`git` over HTTPS, and listing changes, reading diffs, posting comments, and
outcome sync all go through the provider's REST API. The only requirement is
`git` on `PATH` (already in the common prerequisites) and a bot token (below) —
the same token is used for the REST calls and as the `git clone` credential
(sent per-call as an auth header, never written to the checkout's `.git/config`).

## Credentials — required for any review

| Credential | What it does | Notes |
|---|---|---|
| **Bot user + token** | The bot account whose name shows on review threads/comments. | **GitLab:** token with `api` scope. **GitHub:** token with pull-request read+write. Use a dedicated bot account and add it to every reviewed project. |
| **LLM API key** | OpenAI, Anthropic, Gemini, or whatever model your review CLI runs. | One secret, `[agents].llm_api_key` (exported as `LLM_API_KEY`). The agent authenticates with its own login (e.g. `codex login --with-api-key`); see [LLM auth](configuration.md#llm-auth). |

## Optional

| Tool | Needed when |
|---|---|
| **OpenTelemetry collector** | You set `[telemetry].enabled = true`. Receives OTLP/gRPC metrics + spans on the configured endpoint. |
| **systemd or cron** | You want the poller to run on a schedule beyond a one-shot invocation. |

## Verify the install

`uv tool install` checks only `uv` itself; the rest resolve at the first poll.
`bubo doctor` covers the Python side (workspace, env.toml, DB, Codex profile)
but does NOT check that the external CLIs the worker shells out to are on
`PATH`. Run this after `bubo init` to catch a missing tool before the first
cycle — the most common cause of a first-cycle worker failure:

```sh
for bin in uv python3 git codex claude; do
  printf '%-20s %s\n' "$bin" "$(command -v "$bin" 2>/dev/null || echo MISSING)"
done
```

You only need tools for the providers and agents you've enabled in
`config/env.toml` — `MISSING` on the rest is fine.
