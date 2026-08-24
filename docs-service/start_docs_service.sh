#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SERVICE_DIR}/.." && pwd)"

DOCS_HOST="${AIVIRTEACH_DOCS_HOST:-127.0.0.1}"
DOCS_PORT="${AIVIRTEACH_DOCS_PORT:-8780}"
PYTHON_BIN="${AIVIRTEACH_PYTHON:-${PROJECT_DIR}/.venv/bin/python}"

[[ -x "$PYTHON_BIN" ]] || {
  echo "ERROR: Python environment not found: $PYTHON_BIN" >&2
  exit 1
}
[[ "$DOCS_PORT" =~ ^[0-9]+$ ]] && (( DOCS_PORT >= 1 && DOCS_PORT <= 65535 )) || {
  echo "ERROR: AIVIRTEACH_DOCS_PORT must be between 1 and 65535." >&2
  exit 1
}

export PYTHONPATH="${SERVICE_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
cd "$SERVICE_DIR"
exec "$PYTHON_BIN" -m uvicorn docs_service:app --host "$DOCS_HOST" --port "$DOCS_PORT"
