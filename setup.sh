#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$task_root"
voice_python="${VOICE_PYTHON:-python3}"
"$voice_python" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
