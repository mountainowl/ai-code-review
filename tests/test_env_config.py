from __future__ import annotations

from pathlib import Path

from llm_reviewer.env_config import read_config_file, runtime_env


def test_runtime_env_exports_from_env_toml() -> None:
    root = Path("/opt/llm-reviewer")
    cfg = {
        "gitlab": {
            "api_url": "https://gitlab.example/api/v4",
            "bot_username": "review-bot",
            "denied_tools_regex": "^(delete_.*)$",
        },
        "poller": {
            "state_dir": "var",
            "interval_seconds": 123,
        },
        "agent": {
            "prompt_file": "prompts/00-meta.md",
            "model": "gpt-test",
            "reasoning_effort": "high",
            "manual_review_dry_run": False,
            "codex_profile": "reviewer",
            "codex_sandbox": "read-only",
        },
        "secrets": {
            "gitlab_token": "gitlab-secret",
            "openai_api_key": "openai-secret",
            "anthropic_api_key": "anthropic-secret",
            "qwen_api_key": "qwen-secret",
        },
    }

    env = runtime_env(root, cfg)

    assert env["LLM_CODE_REVIEW_ROOT"] == "/opt/llm-reviewer"
    assert env["LLM_CODE_REVIEW_HOME"] == "/opt/llm-reviewer"
    assert env["LLM_CODE_REVIEW_BASE_DIR"] == "/opt/llm-reviewer/var"
    assert env["LLM_CODE_REVIEW_PROMPT"] == "/opt/llm-reviewer/prompts/00-meta.md"
    assert env["REVIEW_MODEL"] == "gpt-test"
    assert env["REVIEW_REASONING_EFFORT"] == "high"
    assert env["REVIEW_DRY_RUN"] == "false"
    assert env["POLL_INTERVAL_SECONDS"] == "123"
    assert env["CODEX_REVIEW_PROFILE"] == "reviewer"
    assert env["CODEX_SANDBOX"] == "read-only"
    assert env["GITLAB_API_URL"] == "https://gitlab.example/api/v4"
    assert env["GITLAB_DENIED_TOOLS_REGEX"] == "^(delete_.*)$"
    assert env["LLM_REVIEWER_GITLAB_USERNAME"] == "review-bot"
    assert env["GITLAB_TOKEN"] == "gitlab-secret"
    assert env["OPENAI_API_KEY"] == "openai-secret"
    assert env["ANTHROPIC_API_KEY"] == "anthropic-secret"
    assert env["QWEN_API_KEY"] == "qwen-secret"


def test_runtime_env_still_accepts_legacy_runtime_section() -> None:
    root = Path("/opt/llm-reviewer")
    cfg = {
        "runtime": {
            "base_dir": "state",
            "prompt": "custom/prompt.md",
            "review_model": "legacy-model",
            "review_reasoning_effort": "low",
            "review_dry_run": False,
            "poll_interval_seconds": 321,
            "codex_review_profile": "legacy-profile",
            "codex_sandbox": "workspace-write",
            "gitlab_api_url": "https://gitlab.legacy/api/v4",
            "gitlab_denied_tools_regex": "^legacy$",
        }
    }

    env = runtime_env(root, cfg)

    assert env["LLM_CODE_REVIEW_BASE_DIR"] == "/opt/llm-reviewer/state"
    assert env["LLM_CODE_REVIEW_PROMPT"] == "/opt/llm-reviewer/custom/prompt.md"
    assert env["REVIEW_MODEL"] == "legacy-model"
    assert env["REVIEW_REASONING_EFFORT"] == "low"
    assert env["REVIEW_DRY_RUN"] == "false"
    assert env["POLL_INTERVAL_SECONDS"] == "321"
    assert env["CODEX_REVIEW_PROFILE"] == "legacy-profile"
    assert env["CODEX_SANDBOX"] == "workspace-write"
    assert env["GITLAB_API_URL"] == "https://gitlab.legacy/api/v4"
    assert env["GITLAB_DENIED_TOOLS_REGEX"] == "^legacy$"


def test_empty_secrets_are_not_exported() -> None:
    env = runtime_env(Path("/opt/llm-reviewer"), {"secrets": {"gitlab_token": "", "openai_api_key": ""}})

    assert "GITLAB_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env


def test_read_config_file_reads_env_toml_without_overlay(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    env_file = config_dir / "env.toml"
    env_file.write_text(
        """
gitlab_url = "https://gitlab.com"

[[projects]]
path = "example/enabled-repo"
enabled = true
""",
        encoding="utf-8",
    )

    cfg = read_config_file(env_file)

    assert cfg["projects"] == [{"path": "example/enabled-repo", "enabled": True}]
