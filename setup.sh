#!/usr/bin/env bash
# One-time setup: create venv inside this folder, install deps, fetch ffmpeg.
# Re-run any time you want to refresh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY="${PY:-python3.13}"
if ! command -v "$PY" >/dev/null 2>&1; then PY="python3"; fi

echo ">> using $($PY --version) at $(command -v "$PY")"

# Keep all model weights inside the project folder so deleting the folder
# really does remove everything. HuggingFace libraries respect HF_HOME for
# their cache root.
export HF_HOME="$ROOT/models"
mkdir -p "$HF_HOME"

if [ ! -d ".venv" ]; then
  echo ">> creating venv at .venv"
  "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo ">> upgrading pip"
python -m pip install --upgrade pip wheel >/dev/null

echo ">> installing python deps"
python -m pip install -r requirements.txt

# Playwright needs chromium; this downloads ~150MB into ~/Library/Caches/ms-playwright
# We can't redirect that to the project folder without further config, so we pin
# PLAYWRIGHT_BROWSERS_PATH and let setup put it there.
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/models/playwright"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
if [ ! -d "$PLAYWRIGHT_BROWSERS_PATH/chromium-"* ]; then
  echo ">> installing chromium for playwright (~150MB) into ./models/playwright/"
  python -m playwright install chromium
fi

# Fetch a static ffmpeg + ffprobe binary into ./bin if not already present.
# imageio-ffmpeg ships a static ffmpeg; we copy/symlink it so the rest of the
# code can just call ./bin/ffmpeg without depending on system PATH.
if [ ! -x "$ROOT/bin/ffmpeg" ]; then
  echo ">> linking imageio-ffmpeg's static binary into ./bin/ffmpeg"
  FFMPEG_PATH="$(python -c 'import imageio_ffmpeg, sys; sys.stdout.write(imageio_ffmpeg.get_ffmpeg_exe())')"
  ln -sf "$FFMPEG_PATH" "$ROOT/bin/ffmpeg"
fi

if [ ! -x "$ROOT/bin/ffprobe" ]; then
  # imageio-ffmpeg doesn't ship ffprobe; download a static one from evermeet.
  echo ">> downloading static ffprobe (darwin arm64) from evermeet.cx"
  TMP="$(mktemp -d)"
  curl -fsSL -o "$TMP/ffprobe.zip" "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"
  unzip -q -o "$TMP/ffprobe.zip" -d "$TMP"
  mv "$TMP/ffprobe" "$ROOT/bin/ffprobe"
  chmod +x "$ROOT/bin/ffprobe"
  rm -rf "$TMP"
fi

# Inter font (used in title cards). OFL-licensed, free to redistribute.
if [ ! -f "$ROOT/assets/fonts/Inter-Bold.ttf" ]; then
  echo ">> downloading Inter font into ./assets/fonts/"
  mkdir -p "$ROOT/assets/fonts"
  TMP="$(mktemp -d)"
  curl -fsSL -o "$TMP/inter.zip" "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip"
  unzip -q -o "$TMP/inter.zip" -d "$TMP/inter"
  for f in Inter-Bold.ttf Inter-Regular.ttf Inter-SemiBold.ttf; do
    cp "$TMP/inter/extras/ttf/$f" "$ROOT/assets/fonts/$f"
  done
  cp "$TMP/inter/LICENSE.txt" "$ROOT/assets/fonts/Inter-LICENSE.txt"
  rm -rf "$TMP"
fi

echo ">> done. launch with: ./run.sh"
