#!/usr/bin/env bash
# ODIN user setup: virtualenv, Python packages and the offline models.
# Needs internet; everything afterwards runs offline.
#
#   scripts/setup.sh
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."
ROOT="$PWD"
VOSK_MODEL="vosk-model-small-en-us-0.15"
VOICE="en_US-ryan-medium"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required (https://docs.astral.sh/uv/). Install it first." >&2
    exit 1
fi

echo "==> Creating the virtualenv"
uv venv --python 3.14 .venv

echo "==> Installing Python packages"
uv pip install --python .venv/bin/python \
    vosk sounddevice numpy piper-tts jeepney

mkdir -p models
if [[ ! -d "models/$VOSK_MODEL" ]]; then
    echo "==> Downloading the speech model (40 MB)"
    curl -fsSL -o /tmp/odin-vosk.zip \
        "https://alphacephei.com/vosk/models/$VOSK_MODEL.zip"
    unzip -q -o /tmp/odin-vosk.zip -d models
    rm -f /tmp/odin-vosk.zip
else
    echo "==> Speech model already present"
fi

if [[ ! -f "models/$VOICE.onnx" ]]; then
    echo "==> Downloading the Piper voice (63 MB)"
    base="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium"
    curl -fsSL -o "models/$VOICE.onnx"      "$base/$VOICE.onnx"
    curl -fsSL -o "models/$VOICE.onnx.json" "$base/$VOICE.onnx.json"
else
    echo "==> Voice already present"
fi

echo
echo "==> Validating"
./odin.sh check || true

cat <<NOTE

If anything above is FAIL for a system package, run:

    sudo scripts/setup-system.sh

Then install the always-on service with:

    scripts/install-service.sh
NOTE
