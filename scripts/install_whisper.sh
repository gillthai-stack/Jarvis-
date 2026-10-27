#!/usr/bin/env bash
# Builds whisper.cpp (local speech recognition) into vendor/whisper.cpp and downloads an English model.
# Safe to run again: it skips what is already done. Needs git and Apple's command line tools
# (xcode-select --install). No Homebrew needed: if cmake is missing it is installed into .venv.
#
#   ./scripts/install_whisper.sh            # base.en model (142 MB, good balance)
#   ./scripts/install_whisper.sh small.en   # more accurate, slower (466 MB)
set -euo pipefail
cd "$(dirname "$0")/.."

WHISPER_TAG="v1.9.5"
MODEL="${1:-base.en}"
DEST="vendor/whisper.cpp"

# SHA-1 checksums published in whisper.cpp's models/README.md.
case "$MODEL" in
  tiny.en)  SHA1="c78c86eb1a8faa21b369bcd33207cc90d64ae9df" ;;
  base.en)  SHA1="137c40403d78fd54d454da0f9bd998f78703390c" ;;
  small.en) SHA1="db8a495a91d927739e50b3fc1cc4c6b8f6c2d022" ;;
  *) echo "Unknown model '$MODEL'. Use tiny.en, base.en or small.en."; exit 1 ;;
esac

if ! xcode-select -p >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
  echo "Apple's command line tools are needed to build whisper.cpp. Run: xcode-select --install"
  echo "then run this script again."
  exit 1
fi

CMAKE=$(command -v cmake || true)
if [ -z "$CMAKE" ]; then
  if [ ! -x .venv/bin/pip ]; then
    echo "cmake is needed. Run ./start.sh once first (it creates .venv), then run this script again."
    exit 1
  fi
  echo "Installing cmake into Jarvis's Python environment…"
  .venv/bin/pip install --quiet "cmake>=3.24"
  CMAKE="$PWD/.venv/bin/cmake"
fi

mkdir -p vendor
if [ ! -d "$DEST/.git" ]; then
  echo "Downloading whisper.cpp $WHISPER_TAG…"
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$WHISPER_TAG" https://github.com/ggml-org/whisper.cpp "$DEST"
else
  current=$(git -C "$DEST" describe --tags --exact-match 2>/dev/null || echo unknown)
  if [ "$current" != "$WHISPER_TAG" ]; then
    echo "Updating whisper.cpp from $current to $WHISPER_TAG…"
    git -C "$DEST" fetch --quiet --depth 1 origin tag "$WHISPER_TAG"
    git -C "$DEST" -c advice.detachedHead=false checkout --quiet "$WHISPER_TAG"
    rm -rf "$DEST/build"
  fi
fi

BIN="$DEST/build/bin/whisper-cli"
if [ ! -x "$BIN" ]; then
  echo "Building whisper.cpp (a few minutes the first time)…"
  "$CMAKE" -S "$DEST" -B "$DEST/build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF >/dev/null
  "$CMAKE" --build "$DEST/build" --config Release -j --target whisper-cli >/dev/null
fi

MODEL_FILE="$DEST/models/ggml-$MODEL.bin"
if [ ! -f "$MODEL_FILE" ] || [ "$(shasum -a 1 "$MODEL_FILE" | cut -d' ' -f1)" != "$SHA1" ]; then
  echo "Downloading the $MODEL speech model…"
  curl -L --fail --progress-bar -o "$MODEL_FILE.part" \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL.bin"
  if [ "$(shasum -a 1 "$MODEL_FILE.part" | cut -d' ' -f1)" != "$SHA1" ]; then
    rm -f "$MODEL_FILE.part"
    echo "The model download didn't match its checksum. Run this script again."
    exit 1
  fi
  mv "$MODEL_FILE.part" "$MODEL_FILE"
fi

echo "Checking it works on whisper.cpp's sample recording…"
"$BIN" -m "$MODEL_FILE" -f "$DEST/samples/jfk.wav" -nt -np 2>/dev/null | sed '/^ *$/d; s/^ */  heard: /'

echo
echo "Local speech recognition is ready."
if [ "$MODEL" != "base.en" ]; then
  echo "Add this line to .env so Jarvis uses this model:"
  echo "  JARVIS_WHISPER_MODEL=$PWD/$MODEL_FILE"
fi
echo "Restart Jarvis (./start.sh) and press the microphone in the holographic interface."
