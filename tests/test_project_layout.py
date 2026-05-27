from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_uses_uv_src_layout() -> None:
    pyproject = ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())

    assert (ROOT / "LICENSE").is_file()
    assert data["project"]["license"] == "MIT"
    assert (ROOT / "src" / "llm_reviewer" / "poller.py").is_file()
    assert (ROOT / "src" / "llm_reviewer" / "codex_runner.py").is_file()
    assert data["tool"]["uv"]["package"] is True
    assert data["project"]["scripts"]["mr-review-poller"] == "llm_reviewer.poller:main"
    assert data["project"]["scripts"]["code-review-codex"] == "llm_reviewer.codex_runner:main"


def test_project_tree_keeps_config_but_not_runtime_checkouts() -> None:
    readme = (ROOT / "README.md").read_text()

    assert (ROOT / "config" / "env.example.toml").is_file()
    assert (ROOT / "docs" / "images" / "llm-reviewer-hero.png").is_file()
    assert (ROOT / "docs" / "images" / "llm-reviewer-avatar-preview.png").is_file()
    assert (ROOT / "assets" / "llm-reviewer.png").is_file()
    assert "docs/images/llm-reviewer-hero.png" in readme
    assert "docs/images/llm-reviewer-avatar-preview.png" in readme
    assert "assets/llm-reviewer.png" in readme
    assert "config/env.toml" in (ROOT / ".gitignore").read_text()
    assert not any(path.name.startswith("secrets.") for path in (ROOT / "config").iterdir())
    assert not (ROOT / "config" / "config.env").exists()
    assert not (ROOT / "config" / "poller.toml").exists()
    assert (ROOT / "prompts" / "00-meta.md").is_file()
    assert (ROOT / "skills" / "code-reviewer" / "SKILL.md").is_file()
    assert not (ROOT / "root" / "usr" / "local" / "llm-code-review").exists()
    assert not any((ROOT / "var").glob("work/**/.git"))
