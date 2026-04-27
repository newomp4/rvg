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

# pin model cache to the project folder
export HF_HOME="$ROOT/models"
# allow MPS to silently fall back to CPU for ops that aren't implemented
export PYTORCH_ENABLE_MPS_FALLBACK=1

exec python -m app "$@"
