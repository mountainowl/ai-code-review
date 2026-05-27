from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_uses_uv_src_layout() -> None:
    pyproject = ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())

    assert (ROOT / "src" / "llm_reviewer" / "poller.py").is_file()
    assert (ROOT / "src" / "llm_reviewer" / "codex_runner.py").is_file()
    assert data["tool"]["uv"]["package"] is True
    assert data["project"]["scripts"]["mr-review-poller"] == "llm_reviewer.poller:main"
    assert data["project"]["scripts"]["code-review-codex"] == "llm_reviewer.codex_runner:main"


def test_project_tree_keeps_config_but_not_runtime_checkouts() -> None:
    assert (ROOT / "config" / "secrets.env.example").is_file()
    assert (ROOT / "config" / "config.env").is_file()
    assert (ROOT / "config" / "poller.toml").is_file()
    assert (ROOT / "prompts" / "00-meta.md").is_file()
    assert (ROOT / "skills" / "code-reviewer" / "SKILL.md").is_file()
    assert not (ROOT / "root" / "usr" / "local" / "llm-code-review").exists()
    assert not any((ROOT / "var").glob("work/**/.git"))
