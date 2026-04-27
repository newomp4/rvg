#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [ ! -d ".venv" ]; then
  echo "venv missing — run ./setup.sh first" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
exec python -m app "$@"
