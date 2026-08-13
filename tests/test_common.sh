#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/libvirt/scripts/common.sh"

as_root() {
  [[ " $* " == *" domifaddr expected-vm "* ]] || return 1

  if [[ " $* " == *" --source agent "* ]]; then
    printf '%s\n' \
      ' Name       MAC address          Protocol     Address' \
      '-------------------------------------------------------------------------------' \
      ' lo         00:00:00:00:00:00    ipv4         127.0.0.1/8' \
      ' ens3       52:54:00:d6:1d:1a    ipv4         192.168.122.210/24'
  fi
}

actual="$(get_vm_ip expected-vm)"
[[ "$actual" == "192.168.122.210" ]] || {
  printf 'Expected 192.168.122.210, got %s\n' "$actual" >&2
  exit 1
}

echo "common.sh checks passed."
