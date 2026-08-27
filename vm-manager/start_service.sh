#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SERVICE_DIR}/.." && pwd)"

: "${AIVIRTEACH_API_TOKEN:?Set AIVIRTEACH_API_TOKEN before starting the API}"

API_HOST="${AIVIRTEACH_API_HOST:-127.0.0.1}"
API_PORT="${AIVIRTEACH_API_PORT:-8760}"
PYTHON_BIN="${AIVIRTEACH_PYTHON:-${PROJECT_DIR}/.venv/bin/python}"

[[ -x "$PYTHON_BIN" ]] || {
  echo "ERROR: Python environment not found: $PYTHON_BIN" >&2
  echo "Create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
}

if [[ $EUID -ne 0 ]] && ! sudo -n true >/dev/null 2>&1; then
  echo "ERROR: Run the API as root or configure passwordless sudo for its service account." >&2
  exit 1
fi

[[ "$API_PORT" =~ ^[0-9]+$ ]] && (( API_PORT >= 1 && API_PORT <= 65535 )) || {
  echo "ERROR: AIVIRTEACH_API_PORT must be between 1 and 65535." >&2
  exit 1
}

export PYTHONPATH="${SERVICE_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
cd "$SERVICE_DIR"
exec "$PYTHON_BIN" -m uvicorn service:app --host "$API_HOST" --port "$API_PORT"
