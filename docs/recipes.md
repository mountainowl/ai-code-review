# Recipes

Copy-paste setups, top to bottom — swap the `< >` placeholders for your own
values. Codex is the bundled default; [Claude](#claude) covers reviewing with
Claude instead.

!!! tip "Codex or Claude"
    Bubo runs the review through a small wrapper around your agent CLI.
    **Codex is bundled and works out of the box**, so the recipes below use
    it. Prefer **Claude**? See [Claude](#claude).

---

## Codex (OpenAI) — GitLab

The fastest path to a first review on a GitLab merge request.

### 1. Prerequisites

Install these once and put them on `PATH` (see
[prerequisites](prerequisites.md) for platform-specific commands):

- **uv**, **Python 3.14+**, **Git** (Git clones each MR over HTTPS — no `glab` needed)
- **Codex CLI**, authenticated to OpenAI
- **Superpowers + the `code-reviewer` skill** in your Codex config

### 2. Install Bubo

```sh
uv tool install bubo
bubo init           # writes ~/.codex/config.toml (with [profiles.bubo]),
                    # ~/.claude/settings.json, the workspace, and the SQLite DB
```

Want the bleeding edge? Track `main` instead:
`uv tool install git+https://github.com/mountainowl/bubo`.

### 3. Configure

`bubo init` seeds the config at
`~/.local/share/bubo/config/env.toml`. Open it and fill in the minimum:

```toml
[scm]
provider = "gitlab"

[gitlab]
token = "<glpat-your-token>"        # GitLab token with `api` scope

[agents]
llm_model        = "gpt-5.5"            # bubo init templates this into the Codex profile
llm_model_effort = "medium"
llm_api_key      = "<sk-your-openai-key>"

[[projects]]
path = "<your-group>/<your-repo>"   # repos Bubo should watch
enabled = true
```

Authenticate Codex once with that key: `codex login --with-api-key` (reads the
key on stdin). bubo runs the reviewer under a strict env allowlist, so the key
is taken from Codex's own login rather than injected into the agent's
environment.

### 4. Verify and run

```sh
bubo doctor          # checks workspace, config, DB, and the Codex profile block
bubo-poller          # one poll cycle; reviews open MRs, then exits
```

The first run is **dry-run by default** (`[review].dry_run = true`): Bubo
plans findings and writes them to SQLite but posts nothing. Read a
transcript under `~/.local/share/bubo/var/reports/`. When it looks right,
flip `[review].dry_run = false` to start posting inline review threads.

### 5. Schedule it (optional)

`bubo init` rendered ready-to-install cron + systemd units under
`~/.local/share/bubo/deploy/templates/`. See [operate](operate.md) for
the install steps.

---

## Codex (OpenAI) — GitHub

Same as the GitLab recipe, with two changes (still just `git` — no `gh` CLI):

- Use a **GitHub token** with pull-request read+write.
- Set the provider to GitHub:

```toml
[scm]
provider = "github"

[github]
token = "<ghp-your-token>"

[agents]
llm_model        = "gpt-5.5"
llm_model_effort = "medium"
llm_api_key      = "<sk-your-openai-key>"

[[projects]]
path = "<owner>/<repo>"
enabled = true
```

Run `bubo-poller` once `[scm].provider = "github"` is set, or force the
provider for a single run with `BUBO_PROVIDER=github bubo-poller`.

---

## Self-hosted / in-house model (OpenAI-compatible)

The strongest version of Bubo's compliance story: when your org runs its own
OpenAI-compatible gateway — Azure OpenAI, vLLM/TGI, LiteLLM, or an internal
proxy — code, diffs, review state, **and the model call** all stay on your
infrastructure. Nothing leaves.

**The one thing to know:** Bubo never calls the model directly — it shells out to
an agent CLI (Codex by default). Set `[agents].llm_base_url` to your endpoint and
`bubo init` wires the Codex profile to it for you (writes a `[model_providers]`
block and points `[profiles.bubo]` at it); the key is read from the environment
at request time.

### 1. Install + configure

```sh
uv tool install bubo
bubo init      # seeds env.toml, writes the agent profile + DB
```

Then set the endpoint in `$BUBO_ROOT/config/env.toml` and re-run `bubo init` so
it re-templates the profile:

```toml
[agents]
llm_model    = "<internal-model-name>"      # also used for the cost label
llm_api_key  = "${LLM_API_KEY}"             # read from the env at request time
llm_base_url = "https://llm.corp.internal/v1"   # your OpenAI-compatible endpoint
```

```sh
bubo init      # re-templates ~/.codex/config.toml with the model-provider block
bubo doctor
```

**Security note:** a custom `llm_base_url` is the one mode where the key reaches
the agent's environment (an OpenAI-compatible endpoint reads it there). That's
inherent to this setup; everything still stays on your infrastructure.

If you track cost, set the `[telemetry]` `*_per_1m` rows to your gateway's rates
(see the [configuration reference](configuration.md#telemetry)).

### 2. Pre-flight the sandbox (the enterprise Linux gotcha)

On hardened Linux hosts — Ubuntu with the AppArmor unprivileged-user-namespace
restriction — Codex's default `read-only` sandbox uses bubblewrap and can fail to
initialize, and a failed sandbox can surface as a misleading clean "no findings"
review. Enterprise hosts are exactly where this bites. Fix it once during
bring-up: see [Troubleshooting](troubleshooting.md).

### 3. Verify before going live

Keep `[review].dry_run = true`, run `bubo-poller` once, and read a transcript
under `var/reports/` to confirm findings come through your in-house model. Then
flip `dry_run = false`.

!!! note "Not OpenAI-compatible?"
    The escape hatch is `[agents].reviewer_command` — override the whole command
    with any CLI that takes the prompt and returns Bubo's JSON findings contract.
    You're locked to neither Codex nor OpenAI.

---

## Claude

Bubo runs the review through your agent CLI, so Claude works like Codex — only
the CLI it calls changes. Install the Claude CLI, point `[agents].reviewer_command`
at it, and set the model:

```toml
[agents]
reviewer_command = ["claude", "-p"]
llm_model        = "<your-claude-model>"
llm_model_effort = "medium"
llm_api_key      = "<your-anthropic-key>"
```

Authenticate the Claude CLI with that key the way its docs describe (e.g.
`ANTHROPIC_API_KEY` in your shell, or `claude` login) — as with Codex, bubo runs
the reviewer under a strict env allowlist rather than injecting the key. Verify it
like Codex: keep `[review].dry_run = true`, run `bubo-poller` once, and read the
transcript under `var/reports/` before you flip `dry_run` to `false`.
