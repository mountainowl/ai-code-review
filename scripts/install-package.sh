#!/bin/sh
set -eu

ROOT="${LLM_REVIEWER_INSTALL_ROOT:-${LLM_CODE_REVIEW_ROOT:-$HOME/.local/share/llm-reviewer}}"
SOURCE=""
INSTALL_AGENT_CONFIG=0
USE_SUDO=0

usage() {
    echo "usage: $0 [--source DIR] [--root DIR] [--sudo] [--install-agent-config]" >&2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) SOURCE="$2"; shift 2 ;;
        --root) ROOT="$2"; shift 2 ;;
        --sudo) USE_SUDO=1; shift ;;
        --install-agent-config) INSTALL_AGENT_CONFIG=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done

if [ -z "$SOURCE" ]; then
    SOURCE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
fi

command -v uv >/dev/null 2>&1 || { echo "uv is required on target" >&2; exit 2; }

as_root() {
    if [ "$USE_SUDO" -eq 1 ]; then
        sudo "$@"
    else
        "$@"
    fi
}

tmp="$(mktemp -d)"
preserved_env=""
trap 'rm -rf "$tmp"; if [ -n "$preserved_env" ]; then rm -f "$preserved_env"; fi' EXIT

(cd "$SOURCE" && COPYFILE_DISABLE=1 tar \
    --exclude=.venv \
    --exclude=.git \
    --exclude=.pytest_cache \
    --exclude=__pycache__ \
    --exclude=config/env.toml \
    --exclude=var/work \
    --exclude=var/log \
    --exclude=var/reports \
    --exclude=var/jobs \
    -cf - .) | tar -xf - -C "$tmp"

if as_root test -f "$ROOT/config/env.toml"; then
    preserved_env="$(mktemp)"
    as_root cp "$ROOT/config/env.toml" "$preserved_env"
fi
if as_root test -d "$ROOT"; then
    as_root find "$ROOT" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
else
    as_root mkdir -p "$ROOT"
fi
(cd "$tmp" && tar -cf - .) | as_root tar -xf - -C "$ROOT"
if [ -n "$preserved_env" ] && [ -f "$preserved_env" ]; then
    as_root mkdir -p "$ROOT/config"
    as_root cp "$preserved_env" "$ROOT/config/env.toml"
elif [ -f "$ROOT/config/env.example.toml" ]; then
    as_root cp "$ROOT/config/env.example.toml" "$ROOT/config/env.toml"
fi
if [ "$USE_SUDO" -eq 1 ]; then
    as_root chown -R "$(id -un):$(id -gn)" "$ROOT"
fi
chmod +x "$ROOT"/bin/* "$ROOT"/scripts/*.sh "$ROOT"/skills/code-review/scripts/*

mkdir -p "$ROOT/var/state" "$ROOT/var/log" "$ROOT/var/work" "$ROOT/var/reports" "$ROOT/var/jobs"
uv sync --locked --no-dev --project "$ROOT"
LLM_CODE_REVIEW_ROOT="$ROOT" "$ROOT/bin/mr-review-poller" --init-db

if [ "$INSTALL_AGENT_CONFIG" -eq 1 ]; then
    mkdir -p "$HOME/.codex/skills" "$HOME/.claude"
    escaped_root="$(printf '%s' "$ROOT" | sed 's/[&|]/\\&/g')"
    sed "s|{{ROOT}}|$escaped_root|g" "$ROOT/deploy/templates/codex-config.toml" > "$HOME/.codex/config.toml"
    sed "s|{{ROOT}}|$escaped_root|g" "$ROOT/deploy/templates/claude-settings.json" > "$HOME/.claude/settings.json"
    ln -sfn "$ROOT/skills/code-review" "$HOME/.codex/skills/code-review"
    ln -sfn "$ROOT/skills/code-reviewer" "$HOME/.codex/skills/code-reviewer"
fi

echo "installed llm-reviewer at $ROOT"
