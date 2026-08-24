#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

usage() {
  cat <<USAGE
Usage: $0 LAB_ID [--source qemu|journal|all] [--lines N] [--follow]

Sources:
  qemu     /var/log/libvirt/qemu/LAB_ID.log (default)
  journal  Host systemd journal entries containing the exact LAB_ID
  all      QEMU log followed by matching journal entries

Options:
  --lines N  Return the latest N lines (default: 200, maximum: 5000)
  --follow   Continue streaming new lines; only valid with qemu or journal
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
[[ $# -ge 1 ]] || { usage; exit 1; }

LAB_ID="$1"
shift
validate_lab_id "$LAB_ID"

SOURCE="qemu"
LINES=200
FOLLOW=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      [[ $# -ge 2 ]] || die "Missing value for --source"
      SOURCE="$2"
      shift
      ;;
    --lines)
      [[ $# -ge 2 ]] || die "Missing value for --lines"
      LINES="$2"
      shift
      ;;
    --follow)
      FOLLOW=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift
done

[[ "$SOURCE" == "qemu" || "$SOURCE" == "journal" || "$SOURCE" == "all" ]] \
  || die "Invalid source '$SOURCE'. Use qemu, journal or all."
[[ "$LINES" =~ ^[0-9]+$ ]] && (( LINES >= 1 && LINES <= 5000 )) \
  || die "--lines must be an integer between 1 and 5000."
[[ "$FOLLOW" != true || "$SOURCE" != "all" ]] \
  || die "--follow cannot be combined with --source all."

require_root_or_sudo
require_command tail

QEMU_LOG="/var/log/libvirt/qemu/${LAB_ID}.log"

show_qemu_log() {
  as_root test -f "$QEMU_LOG" || die "QEMU log not found: $QEMU_LOG"
  if [[ "$FOLLOW" == true ]]; then
    as_root tail -n "$LINES" -F -- "$QEMU_LOG"
  else
    as_root tail -n "$LINES" -- "$QEMU_LOG"
  fi
}

show_journal() {
  require_command journalctl

  # LAB_ID is already restricted to letters, numbers, dot, underscore and dash.
  # Escape regex metacharacters so journalctl --grep matches the literal ID.
  local journal_pattern
  journal_pattern="$(printf '%s' "$LAB_ID" | sed 's/\./\\./g')"

  if [[ "$FOLLOW" == true ]]; then
    as_root journalctl --no-pager -n "$LINES" -f --grep "$journal_pattern"
  else
    as_root journalctl --no-pager -n "$LINES" --grep "$journal_pattern"
  fi
}

case "$SOURCE" in
  qemu)
    show_qemu_log
    ;;
  journal)
    show_journal
    ;;
  all)
    printf '===== QEMU/libvirt log: %s =====\n' "$QEMU_LOG"
    show_qemu_log
    printf '\n===== Host journal entries matching: %s =====\n' "$LAB_ID"
    show_journal
    ;;
esac
