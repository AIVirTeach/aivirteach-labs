#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

: "${AIVIRTEACH_DIAGNOSTIC_TOKEN:?Set AIVIRTEACH_DIAGNOSTIC_TOKEN before starting the diagnostic gateway}"

DIAGNOSTIC_HOST="${AIVIRTEACH_DIAGNOSTIC_HOST:-127.0.0.1}"
DIAGNOSTIC_PORT="${AIVIRTEACH_DIAGNOSTIC_PORT:-8765}"
PYTHON_BIN="${AIVIRTEACH_PYTHON:-${PROJECT_DIR}/.venv/bin/python}"

[[ -x "$PYTHON_BIN" ]] || {
  echo "ERROR: Python environment not found: $PYTHON_BIN" >&2
  exit 1
}
if [[ $EUID -ne 0 ]] && ! sudo -n true >/dev/null 2>&1; then
  echo "ERROR: Run the gateway as root or configure passwordless sudo." >&2
  exit 1
fi
[[ "$DIAGNOSTIC_PORT" =~ ^[0-9]+$ ]] && (( DIAGNOSTIC_PORT >= 1 && DIAGNOSTIC_PORT <= 65535 )) || {
  echo "ERROR: AIVIRTEACH_DIAGNOSTIC_PORT must be between 1 and 65535." >&2
  exit 1
}

cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -m uvicorn gateway_service:app --host "$DIAGNOSTIC_HOST" --port "$DIAGNOSTIC_PORT"
