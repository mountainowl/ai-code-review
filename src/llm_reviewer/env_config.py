from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path
from typing import Any
import tomllib


ENV_CONFIG_NAME = "env.toml"
SECRET_ENV_KEYS = {
    "gitlab_token": "GITLAB_TOKEN",
    "gitlab_personal_access_token": "GITLAB_PERSONAL_ACCESS_TOKEN",
    "glab_token": "GLAB_TOKEN",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "qwen_api_key": "QWEN_API_KEY",
}


def env_config_path(root: Path) -> Path:
    return root / "config" / ENV_CONFIG_NAME


def read_env_config(root: Path) -> dict[str, Any]:
    return read_config_file(env_config_path(root))


def read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing config: {path}")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def runtime_env(root: Path, cfg: dict[str, Any]) -> dict[str, str]:
    runtime = _section(cfg, "runtime")
    poller = _section(cfg, "poller")
    agent = _section(cfg, "agent")
    gitlab = _section(cfg, "gitlab")

    gitlab_url = str(gitlab.get("url", cfg.get("gitlab_url", "https://gitlab.com"))).rstrip("/")
    base_dir = _path_value(root, poller.get("state_dir", runtime.get("base_dir", "var")))
    prompt = _path_value(root, agent.get("prompt_file", runtime.get("prompt", "prompts/00-meta.md")))
    exports = {
        "LLM_CODE_REVIEW_ROOT": str(root),
        "LLM_CODE_REVIEW_HOME": str(root),
        "LLM_CODE_REVIEW_BASE_DIR": str(base_dir),
        "LLM_CODE_REVIEW_PROMPT": str(prompt),
        "REVIEW_MODEL": str(agent.get("model", runtime.get("review_model", "gpt-5.5"))),
        "REVIEW_REASONING_EFFORT": str(
            agent.get("reasoning_effort", runtime.get("review_reasoning_effort", "medium"))
        ),
        "REVIEW_DRY_RUN": _bool_text(agent.get("manual_review_dry_run", runtime.get("review_dry_run", True))),
        "POLL_INTERVAL_SECONDS": str(int(poller.get("interval_seconds", runtime.get("poll_interval_seconds", 900)))),
        "CODEX_REVIEW_PROFILE": str(agent.get("codex_profile", runtime.get("codex_review_profile", "llm-reviewer"))),
        "CODEX_SANDBOX": str(agent.get("codex_sandbox", runtime.get("codex_sandbox", "read-only"))),
        "GITLAB_API_URL": str(gitlab.get("api_url", runtime.get("gitlab_api_url", f"{gitlab_url}/api/v4"))),
        "GITLAB_DENIED_TOOLS_REGEX": str(
            gitlab.get(
                "denied_tools_regex",
                runtime.get("gitlab_denied_tools_regex", "^(delete_.*|merge_merge_request|push_files)$"),
            )
        ),
    }
    if gitlab.get("bot_username"):
        exports["LLM_REVIEWER_GITLAB_USERNAME"] = str(gitlab["bot_username"])
    exports.update(secret_env(cfg))
    return exports


def secret_env(cfg: dict[str, Any]) -> dict[str, str]:
    secrets = cfg.get("secrets") or {}
    if not isinstance(secrets, dict):
        return {}
    exports: dict[str, str] = {}
    for key, env_key in SECRET_ENV_KEYS.items():
        value = secrets.get(key)
        if value:
            exports[env_key] = str(value)
    return exports


def apply_runtime_env(root: Path, cfg: dict[str, Any]) -> None:
    for key, value in runtime_env(root, cfg).items():
        os.environ.setdefault(key, value)
    if os.environ.get("GITLAB_TOKEN") and not os.environ.get("GITLAB_PERSONAL_ACCESS_TOKEN"):
        os.environ["GITLAB_PERSONAL_ACCESS_TOKEN"] = os.environ["GITLAB_TOKEN"]


def shell_exports(root: Path, cfg: dict[str, Any]) -> str:
    lines = [f"export {key}={shlex.quote(value)}" for key, value in runtime_env(root, cfg).items()]
    lines.append('if [ -n "${GITLAB_TOKEN:-}" ] && [ -z "${GITLAB_PERSONAL_ACCESS_TOKEN:-}" ]; then')
    lines.append('  export GITLAB_PERSONAL_ACCESS_TOKEN="$GITLAB_TOKEN"')
    lines.append("fi")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(shell_exports(args.root.resolve(), read_env_config(args.root.resolve())))
    return 0


def _path_value(root: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else root / path


def _section(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    value = cfg.get(name) or {}
    return value if isinstance(value, dict) else {}


def _bool_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).lower()


if __name__ == "__main__":
    raise SystemExit(main())
