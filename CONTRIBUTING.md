# Contributing

LLM Reviewer is intentionally small. Good contributions keep the runtime simple,
generic, and safe to run against private repositories.

## Ground Rules

- Do not commit secrets, private repository names, customer names, tokens, or
  screenshots that expose private code.
- Keep changes generic. Repo-specific review rules belong in examples or local
  config, not in core code.
- Keep pull requests focused. Separate docs, packaging, prompt, and runtime
  behavior changes when possible.
- Add or update tests with code changes.
- Prefer plain Python, shell wrappers, and existing dependencies over adding a
  framework.

## Development

```sh
uv sync --dev
uv run pytest -q
uv run python -m compileall -q src tests
```

Local secrets live in ignored `config/env.toml`.

## Pull Requests

Before opening a PR:

```sh
uv run pytest -q
uv run python -m compileall -q src tests
git diff --check
```

Include the command output or a short verification note in the PR description.

## Useful Areas

- Better line-position mapping for GitLab discussions.
- More SCM adapters while keeping GitLab first.
- More telemetry rollups and dashboards.
- Safer prompt-output parsing.
- Packaging and install docs for new platforms.
