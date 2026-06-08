# Recipes

Copy-paste setups you can follow top to bottom. Each block is meant to be
run verbatim — only the values in `< >` need replacing.

!!! tip "Pick your agent"
    Bubo runs the review through an agent CLI. **Codex (OpenAI) is the
    turnkey path today** and the recipe below is verbatim. A **Claude**
    path is in progress — see [Claude (experimental)](#claude-experimental).

---

## Codex (OpenAI) — GitLab

The fastest path to a first review on a GitLab merge request.

### 1. Prerequisites

Install these once and make sure they're on `PATH` (see
[prerequisites](prerequisites.md) for platform-specific commands):

- **uv**, **Python 3.14+**, **Git**
- **Codex CLI**, authenticated to OpenAI
- **Superpowers + the `code-reviewer` skill** in your Codex config
- **glab** (GitLab CLI) — for cloning/fetching MRs

### 2. Install Bubo

```sh
uv tool install git+https://github.com/mountainowl/bubo@v0.8.0
bubo init           # writes ~/.codex/config.toml (with [profiles.bubo]),
                    # ~/.claude/settings.json, the workspace, and the SQLite DB
```

### 3. Configure

`bubo init` seeds the config at
`~/.local/share/bubo/config/env.toml`. Open it and fill in the minimum:

```toml
[scm]
provider = "gitlab"

[gitlab]
token = "<glpat-your-token>"        # GitLab token with `api` scope

[agents]
llm_model = "gpt-5.5"               # any model your Codex profile drives
llm_api_key = "<sk-your-openai-key>"
llm_api_key_env = "OPENAI_API_KEY"  # the env var the OpenAI/Codex CLI reads

[[projects]]
path = "<your-group>/<your-repo>"   # repos Bubo should watch
enabled = true
```

### 4. Verify and run

```sh
bubo doctor          # checks workspace, config, DB, and the Codex profile block
bubo-poller          # one poll cycle; reviews open MRs, then exits
```

The first run is **dry-run by default** (`[review].dry_run = true`) — Bubo
plans findings and writes them to SQLite but posts nothing. Read a
transcript under `~/.local/share/bubo/var/reports/`, and when the output
looks right, flip `[review].dry_run = false` to start posting inline
review threads.

### 5. Schedule it (optional)

`bubo init` rendered ready-to-install cron + systemd units under
`~/.local/share/bubo/deploy/templates/`. See [operate](operate.md) for
the install steps.

---

## Codex (OpenAI) — GitHub

Identical to the GitLab recipe with three changes:

- Install the **`gh` CLI** instead of `glab`, authenticated to GitHub.
- Use a **GitHub token** with pull-request read+write.
- Set the provider to GitHub:

```toml
[scm]
provider = "github"

[github]
token = "<ghp-your-token>"

[agents]
llm_model = "gpt-5.5"
llm_api_key = "<sk-your-openai-key>"
llm_api_key_env = "OPENAI_API_KEY"

[[projects]]
path = "<owner>/<repo>"
enabled = true
```

Run with `bubo-gh-poller` (it forces the GitHub provider) or with
`bubo-poller` once `[scm].provider = "github"` is set.

---

## Another OpenAI-compatible model

Bubo doesn't hardcode providers — `llm_api_key_env` names the variable
your CLI reads. To point Codex at a different model, change the model
in your Codex profile (`~/.codex/config.toml`, `[profiles.bubo]`) and set:

```toml
[agents]
llm_model = "<your-model>"
llm_api_key = "<your-key>"
llm_api_key_env = "<THE_ENV_VAR_YOUR_CLI_READS>"   # e.g. OPENAI_API_KEY
```

---

## Claude (experimental)

**Status: not yet turnkey.** `bubo init` writes a `~/.claude/settings.json`
(it enables the Superpowers `code-reviewer` skill for *interactive* Claude
Code use), but the automated poller currently drives **Codex only** — there
is no shipped `bubo-claude` runner, and Claude isn't yet registered with
the git/GitLab MCP servers the review needs.

A real Claude review runner is being added in a follow-up. When it lands it
will be marked **experimental** until a first real run confirms its output
parses into findings — because an agent whose output doesn't match the
review contract silently returns *zero* findings, which looks like success.

Until then: use the Codex recipe above, or
[follow the tracking issue](https://github.com/mountainowl/bubo/issues).
