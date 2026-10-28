#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$task_root"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Run ./setup.sh first to install the local Python environment.' >&2
  exit 1
fi
exec .venv/bin/python -m voice_harness "$@"
