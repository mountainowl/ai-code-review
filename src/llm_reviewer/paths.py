"""Single source of truth for filesystem paths used at runtime.

Every Python entry point computes these the same way, so the poller, the
worker fork, the codex_runner, and the test suite all agree on where state,
work directories, and logs live.

Two override hooks:

* ``LLM_CODE_REVIEW_ROOT`` — install root. Set by ``bin/env``; defaults to
  ``~/.local/share/llm-reviewer``. Holds the wrapper scripts, prompts,
  skill assets, and (by default) the runtime state directory below.
* ``LLM_CODE_REVIEW_BASE_DIR`` — runtime state directory. Holds the SQLite
  state file, per-MR worktrees, logs, reports, and rendered prompts.
  Set by ``env_config.runtime_env`` from ``[poller].state_dir`` in TOML.
  Falls back to ``$LLM_CODE_REVIEW_ROOT/var`` when unset, which keeps the
  pre-existing layout intact for installs that do not override it.

The two paths are deliberately decoupled so operators can keep mutable
runtime state on a different volume (e.g. SSD-backed persistent volume
in Kubernetes) from the immutable install (read-only baked image).

Importers should depend on this module rather than redefining the same
constants — that has historically been a source of drift between modules.
"""

from __future__ import annotations

import os
from pathlib import Path

from llm_reviewer.env_config import env_config_path


def _resolve_root() -> Path:
    return Path(
        os.environ.get(
            "LLM_CODE_REVIEW_ROOT",
            Path.home() / ".local" / "share" / "llm-reviewer",
        )
    )


def _resolve_state_root(root: Path) -> Path:
    """Return the directory holding runtime state.

    Honors ``LLM_CODE_REVIEW_BASE_DIR`` when set (absolute or relative to
    cwd). Falls back to ``<install_root>/var`` so legacy installs keep
    their existing layout. The exported env var is normally set by
    ``env_config.runtime_env`` from ``[poller].state_dir`` in TOML.
    """
    raw = os.environ.get("LLM_CODE_REVIEW_BASE_DIR")
    if raw:
        base = Path(raw)
        return base if base.is_absolute() else (root / base).resolve()
    return root / "var"


ROOT = _resolve_root()
CONFIG = env_config_path(ROOT)

# Runtime state — overrideable via LLM_CODE_REVIEW_BASE_DIR.
_STATE = _resolve_state_root(ROOT)
DB = _STATE / "state" / "reviewer.sqlite"
WORK = _STATE / "work"
REPORTS = _STATE / "reports"
JOBS = _STATE / "jobs"
LOGS = _STATE / "log"
RENDERED_PROMPTS = _STATE / "rendered-prompts"

# Install-root-relative (never moves).
DEFAULT_REVIEWER = ROOT / "bin" / "code-review-codex"

__all__ = [
    "CONFIG",
    "DB",
    "DEFAULT_REVIEWER",
    "JOBS",
    "LOGS",
    "RENDERED_PROMPTS",
    "REPORTS",
    "ROOT",
    "WORK",
]
