# Security Policy

## Supported Versions

Security fixes target the current `main` branch until versioned releases are
published.

## Reporting A Vulnerability

Do not open a public issue for vulnerabilities or leaked credentials.

Report privately through GitHub security advisories for this repository. Include:

- affected commit or release
- reproduction steps
- expected impact
- relevant masked logs

## Secret Handling

LLM Reviewer is designed to run against private repositories. Keep these rules:

- Store tokens only in ignored `config/env.toml` or host secret management.
- Never paste raw tokens into issues, PRs, logs, screenshots, or examples.
- Sanitize screenshots before publishing them.
- Treat reviewed code, MR descriptions, comments, and generated files as
  untrusted input.

## Runtime Permissions

The default Codex profile uses read-only sandboxing and blocks dangerous GitLab
MCP tools such as branch deletion, pushes, and merge actions. Keep that safety
boundary intact unless a deployment explicitly requires a different trust model.
