#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

usage() { echo "Usage: $0 {start|stop|force-stop|reboot|status|ip|vnc|credentials|register-console-token|delete} LAB_ID [--yes]"; }
[[ $# -ge 2 ]] || { usage; exit 1; }
ACTION="$1"; LAB_ID="$2"; shift 2
validate_lab_id "$LAB_ID"
if [[ "$ACTION" == "register-console-token" ]]; then
  CONSOLE_TOKEN="${1:?Missing token}"
  validate_console_token "$CONSOLE_TOKEN"
  CONSOLE_TTL_SECONDS="${2:?Missing ttl_seconds}"
  shift 2
fi
CONFIRM=false
[[ "${1:-}" == "--yes" ]] && CONFIRM=true
require_root_or_sudo

DOMAIN_EXISTS=false
as_root virsh --connect qemu:///system dominfo "$LAB_ID" >/dev/null 2>&1 && DOMAIN_EXISTS=true

case "$ACTION" in
  start) [[ "$DOMAIN_EXISTS" == true ]] || die "VM not found"; as_root virsh --connect qemu:///system start "$LAB_ID" ;;
  stop) [[ "$DOMAIN_EXISTS" == true ]] || die "VM not found"; as_root virsh --connect qemu:///system shutdown "$LAB_ID" ;;
  force-stop) [[ "$DOMAIN_EXISTS" == true ]] || die "VM not found"; as_root virsh --connect qemu:///system destroy "$LAB_ID" ;;
  reboot) [[ "$DOMAIN_EXISTS" == true ]] || die "VM not found"; as_root virsh --connect qemu:///system reboot "$LAB_ID" ;;
  status) [[ "$DOMAIN_EXISTS" == true ]] || die "VM not found"; as_root virsh --connect qemu:///system dominfo "$LAB_ID" ;;
  ip)
    [[ "$DOMAIN_EXISTS" == true ]] || die "VM not found"
    IP="$(get_vm_ip "$LAB_ID")"
    [[ -n "$IP" ]] || die "No IPv4 address reported yet."
    printf '%s\n' "$IP"
    ;;
  vnc)
    [[ "$DOMAIN_EXISTS" == true ]] || die "VM not found"
    DISPLAY="$(as_root virsh --connect qemu:///system vncdisplay "$LAB_ID")"
    printf 'display=%s\n' "$DISPLAY"
    [[ "$DISPLAY" =~ ^:([0-9]+)$ ]] && printf 'host=127.0.0.1\nport=%s\n' "$((5900 + BASH_REMATCH[1]))"
    ;;
  credentials)
    CRED_FILE="${STATE_DIR}/${LAB_ID}/credentials.txt"
    [[ -f "$CRED_FILE" ]] || die "Credential file not found"
    as_root cat "$CRED_FILE"
    ;;
  register-console-token)
    CRED_FILE="${STATE_DIR}/${LAB_ID}/credentials.txt"
    [[ -f "$CRED_FILE" ]] || die "Credential file not found"
    RDP_TARGET_PORT="$(grep '^rdp_port=' "$CRED_FILE" | cut -d= -f2)"
    [[ -n "$RDP_TARGET_PORT" ]] || die "rdp_port not found in credentials file"

    [[ "$DOMAIN_EXISTS" == true ]] || die "VM not found"
    IP="$(get_vm_ip "$LAB_ID")"
    [[ -n "$IP" ]] || die "No IPv4 address reported yet."

    as_root install -d -m 0750 -o root -g libvirt "$CONSOLE_TOKEN_DIR"
    as_root find "$CONSOLE_TOKEN_DIR" -maxdepth 1 -type f -mmin "+$((CONSOLE_TTL_SECONDS / 60))" -delete

    as_root bash -c "umask 077; printf '%s: %s:%s\n' '$CONSOLE_TOKEN' '$IP' '$RDP_TARGET_PORT' > '${CONSOLE_TOKEN_DIR}/${CONSOLE_TOKEN}'"
    ;;
  delete)
    [[ "$CONFIRM" == true ]] || die "Deletion requires --yes"
    if [[ "$DOMAIN_EXISTS" == true ]]; then
      as_root virsh --connect qemu:///system destroy "$LAB_ID" >/dev/null 2>&1 || true
      as_root virsh --connect qemu:///system undefine "$LAB_ID" --nvram >/dev/null 2>&1 \
        || as_root virsh --connect qemu:///system undefine "$LAB_ID" >/dev/null 2>&1 || true
    fi
    as_root rm -f "${LABS_DIR}/${LAB_ID}.qcow2"
    as_root rm -rf "${SEEDS_DIR}/${LAB_ID}" "${STATE_DIR}/${LAB_ID}"
    log "Deleted $LAB_ID"
    ;;
  *) usage; exit 1 ;;
esac
