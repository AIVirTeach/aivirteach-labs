#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/vm-manager/libvirt/scripts/vm-logs.sh"

"$SCRIPT" --help | grep -q 'Usage:'

if "$SCRIPT" 'invalid lab' >/dev/null 2>&1; then
  echo "Expected invalid lab ID to fail." >&2
  exit 1
fi

if "$SCRIPT" lab-001 --lines 0 >/dev/null 2>&1; then
  echo "Expected --lines 0 to fail." >&2
  exit 1
fi

if "$SCRIPT" lab-001 --lines 5001 >/dev/null 2>&1; then
  echo "Expected --lines 5001 to fail." >&2
  exit 1
fi

if "$SCRIPT" lab-001 --source arbitrary >/dev/null 2>&1; then
  echo "Expected invalid source to fail." >&2
  exit 1
fi

if "$SCRIPT" lab-001 --source all --follow >/dev/null 2>&1; then
  echo "Expected --source all --follow to fail." >&2
  exit 1
fi

echo "vm-logs.sh argument checks passed."
