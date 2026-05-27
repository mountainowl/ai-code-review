from __future__ import annotations

import tomllib
from pathlib import Path

from llm_reviewer.poller import normalize_config


ROOT = Path(__file__).resolve().parents[1]


def test_default_poller_uses_only_sample_repos() -> None:
    config = tomllib.loads((ROOT / "config" / "env.example.toml").read_text())
    projects = {item["path"]: item.get("enabled", True) for item in config["projects"]}

    assert projects == {"example/enabled-repo": True, "example/disabled-repo": False}
    assert all(path.startswith("example/") for path in projects)


def test_default_poller_review_limits() -> None:
    config = tomllib.loads((ROOT / "config" / "env.example.toml").read_text())

    assert config["review"]["max_merge_requests_per_poll"] == 8
    assert config["review"]["max_findings_per_merge_request"] == 8


def test_default_runtime_config_is_consolidated_in_env_toml() -> None:
    config = tomllib.loads((ROOT / "config" / "env.example.toml").read_text())

    assert "runtime" not in config
    assert "post_summary" not in config
    assert config["agent"]["model"] == "gpt-5.5"
    assert config["agent"]["reasoning_effort"] == "medium"
    assert config["poller"]["interval_seconds"] == 900


def test_new_config_names_normalize_to_internal_poller_keys() -> None:
    config = normalize_config(
        {
            "gitlab": {"url": "https://gitlab.example"},
            "review": {
                "dry_run": False,
                "max_merge_requests_per_poll": 11,
                "max_findings_per_merge_request": 7,
                "timeout_seconds": 1234,
            },
            "poller": {"target_merge_request_iid": 42},
            "agent": {"model": "review-model", "reviewer_command": ["reviewer"]},
        }
    )

    assert config["gitlab_url"] == "https://gitlab.example"
    assert config["dry_run"] is False
    assert config["max_reviews_per_run"] == 11
    assert config["max_findings_per_review"] == 7
    assert config["review_timeout_seconds"] == 1234
    assert config["target_mr_iid"] == 42
    assert config["reviewer_command"] == ["reviewer"]
    assert config["model"] == "review-model"
