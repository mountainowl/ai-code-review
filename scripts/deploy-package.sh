#!/bin/sh
set -eu

usage() {
    echo "usage: $0 user@host [--root DIR] [--sudo] [--install-agent-config]" >&2
}

if [ "$#" -lt 1 ]; then
    usage
    exit 2
fi

TARGET="$1"
shift
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
REMOTE_ARGS=""
for arg in "$@"; do
    quoted="$(printf "%s" "$arg" | sed "s/'/'\\\\''/g; s/^/'/; s/$/'/")"
    REMOTE_ARGS="$REMOTE_ARGS $quoted"
done

(cd "$PROJECT_ROOT" && COPYFILE_DISABLE=1 tar \
    --exclude=.venv \
    --exclude=.git \
    --exclude=.pytest_cache \
    --exclude=__pycache__ \
    --exclude=config/env.toml \
    --exclude=var/work \
    --exclude=var/log \
    --exclude=var/reports \
    --exclude=var/jobs \
    -cf - .) | ssh "$TARGET" "tmp=\$(mktemp -d); trap 'rm -rf \"\$tmp\"' EXIT; tar -xf - -C \"\$tmp\"; sh \"\$tmp/scripts/install-package.sh\" --source \"\$tmp\" $REMOTE_ARGS"
