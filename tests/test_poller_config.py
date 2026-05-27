from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_default_poller_uses_sample_repos_not_longtail_repos() -> None:
    config = tomllib.loads((ROOT / "config" / "env.example.toml").read_text())
    projects = {item["path"]: item.get("enabled", True) for item in config["projects"]}

    assert projects == {"example/enabled-repo": True, "example/disabled-repo": False}
    assert all(not path.startswith("longtaildev/") for path in projects)


def test_default_poller_review_limits() -> None:
    config = tomllib.loads((ROOT / "config" / "env.example.toml").read_text())

    assert config["max_reviews_per_run"] == 8
    assert config["max_findings_per_review"] == 8


def test_default_runtime_config_is_consolidated_in_env_toml() -> None:
    config = tomllib.loads((ROOT / "config" / "env.example.toml").read_text())

    assert config["runtime"]["review_model"] == "gpt-5.5"
    assert config["runtime"]["review_reasoning_effort"] == "medium"
    assert config["runtime"]["poll_interval_seconds"] == 900
