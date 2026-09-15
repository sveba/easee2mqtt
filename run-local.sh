#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR="${VENV_DIR:-env}"
REQ_FILE="$(mktemp)"
trap 'rm -f "$REQ_FILE"' EXIT

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

python - <<'PY' > "$REQ_FILE"
from pathlib import Path

data = Path("requirements.txt").read_bytes()
for encoding in ("utf-8-sig", "utf-16"):
    try:
        print(data.decode(encoding), end="")
        break
    except UnicodeDecodeError:
        pass
PY

python -m pip install -r "$REQ_FILE"
python easee2mqtt.py
