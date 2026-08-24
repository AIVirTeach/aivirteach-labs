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

assert_token_rejected() {
  local token="$1"
  if (validate_console_token "$token") 2>/dev/null; then
    printf 'Expected validate_console_token to reject: %s\n' "$token" >&2
    exit 1
  fi
}

assert_token_accepted() {
  local token="$1"
  if ! (validate_console_token "$token") 2>/dev/null; then
    printf 'Expected validate_console_token to accept: %s\n' "$token" >&2
    exit 1
  fi
}

# Rejections: path traversal, shell metacharacters, command substitution, embedded newline,
# and an over-length token.
assert_token_rejected '../../etc/passwd'
assert_token_rejected 'abc;touch /tmp/pwned'
assert_token_rejected 'abc`touch /tmp/pwned`'
assert_token_rejected 'abc$(touch /tmp/pwned)'
assert_token_rejected "$(printf 'abc\ndef')"
assert_token_rejected "$(printf 'a%.0s' $(seq 1 129))"

# Acceptances: base64url charset (what randomBytes(32).toString('base64url') produces on the
# server side), and the 128-char maximum length boundary.
assert_token_accepted 'MZ8pQ1r-KxT_9Lm2Vn7eYs0FhU4bCwXaJgIdOtRk3zN5qP6'
assert_token_accepted "$(printf 'a%.0s' $(seq 1 128))"

echo "common.sh checks passed."
