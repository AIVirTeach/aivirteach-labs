#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for script in "$ROOT"/scripts/*.sh; do
  echo "bash -n: $script"
  bash -n "$script"
done
grep -q 'qemu-kvm' "$ROOT/scripts/install-host.sh"
grep -q 'cloud-localds' "$ROOT/scripts/build-base-image.sh"
grep -q 'qemu-img create' "$ROOT/scripts/create-learner-vm.sh"
grep -q 'xrdp' "$ROOT/scripts/build-base-image.sh"
echo "Static checks passed."
