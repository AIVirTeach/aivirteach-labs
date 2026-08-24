#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

FORCE=false
usage() { echo "Usage: $0 [--force]"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=true ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
  shift
done

require_root_or_sudo
for cmd in curl qemu-img cloud-localds virt-install virsh openssl sha256sum base64; do
  require_command "$cmd"
done
ensure_storage_layout
ensure_default_network

SOURCE_IMAGE="${BASE_DIR}/${UBUNTU_IMAGE_NAME}"
CHECKSUM_FILE="${BASE_DIR}/SHA256SUMS"
BUILDER_DISK="${BASE_DIR}/${BUILDER_VM_NAME}.qcow2"
FINAL_IMAGE="${BASE_DIR}/${GOLDEN_IMAGE_NAME}"
BUILD_DIR="${SEEDS_DIR}/${BUILDER_VM_NAME}"
SEED_ISO="${BUILD_DIR}/seed.iso"
USER_DATA="${BUILD_DIR}/user-data.yaml"
META_DATA="${BUILD_DIR}/meta-data.yaml"

if [[ -e "$FINAL_IMAGE" && "$FORCE" != true ]]; then
  die "Golden image exists: $FINAL_IMAGE. Use --force to replace it."
fi

if [[ -e "$FINAL_IMAGE" && "$FORCE" == true ]] \
    && compgen -G "${LABS_DIR}/*.qcow2" >/dev/null; then
  die "Refusing to replace the golden image while learner overlays exist in $LABS_DIR."
fi

if as_root virsh --connect qemu:///system dominfo "$BUILDER_VM_NAME" >/dev/null 2>&1; then
  [[ "$FORCE" == true ]] || die "Builder domain already exists: $BUILDER_VM_NAME"
  as_root virsh --connect qemu:///system destroy "$BUILDER_VM_NAME" >/dev/null 2>&1 || true
  as_root virsh --connect qemu:///system undefine "$BUILDER_VM_NAME" --nvram >/dev/null 2>&1 \
    || as_root virsh --connect qemu:///system undefine "$BUILDER_VM_NAME" >/dev/null 2>&1 \
    || true
fi

if [[ "$FORCE" == true ]]; then
  as_root rm -f "$FINAL_IMAGE" "$BUILDER_DISK"
  as_root rm -rf "$BUILD_DIR"
fi

log "Downloading Ubuntu ${UBUNTU_RELEASE} released cloud image..."
as_root curl -fL --retry 4 --retry-delay 3 -o "${SOURCE_IMAGE}.tmp" \
  "${UBUNTU_IMAGE_BASE_URL}/${UBUNTU_IMAGE_NAME}"
as_root curl -fL --retry 4 --retry-delay 3 -o "${CHECKSUM_FILE}.tmp" \
  "${UBUNTU_IMAGE_BASE_URL}/SHA256SUMS"
as_root mv "${SOURCE_IMAGE}.tmp" "$SOURCE_IMAGE"
as_root mv "${CHECKSUM_FILE}.tmp" "$CHECKSUM_FILE"

EXPECTED_LINE="$(grep -E "[ *]${UBUNTU_IMAGE_NAME}$" "$CHECKSUM_FILE" | head -n1 || true)"
[[ -n "$EXPECTED_LINE" ]] || die "Image not found in SHA256SUMS."
printf '%s\n' "$EXPECTED_LINE" | (cd "$BASE_DIR" && sha256sum --check -)

as_root qemu-img convert -p -O qcow2 "$SOURCE_IMAGE" "$BUILDER_DISK"
as_root qemu-img resize "$BUILDER_DISK" "$GOLDEN_IMAGE_SIZE"
as_root chown libvirt-qemu:kvm "$BUILDER_DISK"
as_root chmod 0660 "$BUILDER_DISK"

as_root install -d -m 0770 -o root -g kvm "$BUILD_DIR"
command -v setfacl >/dev/null 2>&1 && as_root setfacl -m u:libvirt-qemu:rwx,m:rwx "$BUILD_DIR"

BUILDER_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | head -c 20)"
BUILDER_PASSWORD_HASH="$(openssl passwd -6 "$BUILDER_PASSWORD")"
as_root bash -c "umask 077; printf '%s\n' '$BUILDER_PASSWORD' > '$BUILD_DIR/builder-password.txt'"

CUSTOM_B64=""
if [[ -n "$CUSTOMIZE_SCRIPT" ]]; then
  [[ -f "$CUSTOMIZE_SCRIPT" ]] || die "CUSTOMIZE_SCRIPT not found: $CUSTOMIZE_SCRIPT"
  CUSTOM_B64="$(base64 -w0 "$CUSTOMIZE_SCRIPT")"
fi

USER_DATA_TMP="$(mktemp)"
cat > "$USER_DATA_TMP" <<CLOUDCFG
#cloud-config
hostname: ${BUILDER_VM_NAME}
manage_etc_hosts: true
ssh_pwauth: true
disable_root: true

users:
  - default
  - name: builder
    shell: /bin/bash
    groups: [adm, sudo]
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    lock_passwd: false
    hashed_passwd: "${BUILDER_PASSWORD_HASH}"
  - name: learner
    shell: /bin/bash
    groups: [adm, sudo, video, render]
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    lock_passwd: true

write_files:
  - path: /etc/aivirteach/image-version
    owner: root:root
    permissions: "0644"
    content: |
      ${GOLDEN_IMAGE_NAME}
  - path: /usr/local/sbin/aivirteach-firstboot
    owner: root:root
    permissions: "0755"
    content: |
      #!/usr/bin/env bash
      set -e
      ssh-keygen -A
      install -d -m 0755 /run/aivirteach
      systemctl restart ssh || true
      systemctl restart qemu-guest-agent || true
      systemctl restart xrdp || true
  - path: /etc/systemd/system/aivirteach-firstboot.service
    owner: root:root
    permissions: "0644"
    content: |
      [Unit]
      Description=AIVirTeach cloned VM first-boot preparation
      After=network.target
      ConditionPathExists=!/var/lib/aivirteach/firstboot-complete

      [Service]
      Type=oneshot
      ExecStart=/usr/local/sbin/aivirteach-firstboot
      ExecStartPost=/usr/bin/install -D -m 0644 /dev/null /var/lib/aivirteach/firstboot-complete
      RemainAfterExit=yes

      [Install]
      WantedBy=multi-user.target
CLOUDCFG

if [[ -n "$CUSTOM_B64" ]]; then
  cat >> "$USER_DATA_TMP" <<CUSTOMCFG
  - path: /usr/local/sbin/aivirteach-customize
    owner: root:root
    permissions: "0755"
    encoding: b64
    content: ${CUSTOM_B64}
CUSTOMCFG
fi

cat >> "$USER_DATA_TMP" <<'CLOUDEND'

runcmd:
  - [bash, -lc, "echo 'lightdm shared/default-x-display-manager select lightdm' | debconf-set-selections || true"]
  - [bash, -lc, "apt-get update"]
  - [bash, -lc, "DEBIAN_FRONTEND=noninteractive apt-get install -y xfce4 xfce4-goodies xrdp xorgxrdp dbus-x11 qemu-guest-agent openssh-server git curl wget vim python3 python3-venv python3-pip"]
  - [bash, -lc, "install -d -m 0755 /etc/aivirteach /opt/aivirteach /home/learner/course"]
  - [bash, -lc, "printf '%s\n' 'unset DBUS_SESSION_BUS_ADDRESS' 'unset XDG_RUNTIME_DIR' 'exec startxfce4' > /home/learner/.xsession"]
  - [bash, -lc, "chown -R learner:learner /home/learner"]
  - [bash, -lc, "adduser xrdp ssl-cert || true"]
  - [bash, -lc, "systemctl enable qemu-guest-agent ssh xrdp aivirteach-firstboot.service"]
  - [bash, -lc, "if [ -x /usr/local/sbin/aivirteach-customize ]; then /usr/local/sbin/aivirteach-customize; fi"]
  - [bash, -lc, "passwd -l learner || true"]
  - [bash, -lc, "apt-get clean"]
  - [bash, -lc, "rm -rf /tmp/* /var/tmp/* /var/lib/apt/lists/*"]
  - [bash, -lc, "rm -f /etc/ssh/ssh_host_*"]
  - [bash, -lc, "userdel -r builder || true"]
  - [bash, -lc, "rm -f /var/lib/aivirteach/firstboot-complete"]
  - [bash, -lc, "touch /etc/aivirteach/image-ready"]
  - [bash, -lc, "cloud-init clean --logs --machine-id"]

power_state:
  delay: now
  mode: poweroff
  message: AIVirTeach golden image build completed
  timeout: 60
  condition: true
CLOUDEND

as_root mv "$USER_DATA_TMP" "$USER_DATA"
as_root chown root:kvm "$USER_DATA"
as_root chmod 0640 "$USER_DATA"

META_DATA_TMP="$(mktemp)"
cat > "$META_DATA_TMP" <<METACFG
instance-id: ${BUILDER_VM_NAME}-$(date +%s)
local-hostname: ${BUILDER_VM_NAME}
METACFG
as_root mv "$META_DATA_TMP" "$META_DATA"
as_root chown root:kvm "$META_DATA"
as_root chmod 0640 "$META_DATA"

as_root cloud-localds "$SEED_ISO" "$USER_DATA" "$META_DATA"
as_root chown libvirt-qemu:kvm "$SEED_ISO"
as_root chmod 0640 "$SEED_ISO"

OSINFO="$(choose_osinfo)"
as_root virt-install \
  --connect qemu:///system \
  --name "$BUILDER_VM_NAME" \
  --memory "$BUILDER_MEMORY_MB" \
  --vcpus "$BUILDER_VCPUS" \
  --cpu host-passthrough \
  --osinfo "$OSINFO" \
  --import \
  --disk "path=${BUILDER_DISK},format=qcow2,bus=virtio" \
  --disk "path=${SEED_ISO},device=cdrom,readonly=on" \
  --network "network=${LIBVIRT_NETWORK},model=virtio" \
  --graphics vnc,listen=127.0.0.1 \
  --video virtio \
  --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0 \
  --noautoconsole

cat <<BUILDINFO
Builder started. Temporary login: builder / $BUILDER_PASSWORD
VNC: sudo virsh vncdisplay $BUILDER_VM_NAME
BUILDINFO

START_TIME="$(date +%s)"
while true; do
  STATE="$(as_root virsh --connect qemu:///system domstate "$BUILDER_VM_NAME" 2>/dev/null | tr -d '\r' || true)"
  [[ "$STATE" == "shut off" ]] && break
  NOW="$(date +%s)"
  if (( NOW - START_TIME > BUILD_TIMEOUT_SECONDS )); then
    die "Builder timed out. Inspect with sudo virsh console $BUILDER_VM_NAME"
  fi
  sleep 10
done

as_root virsh --connect qemu:///system undefine "$BUILDER_VM_NAME" --nvram >/dev/null 2>&1 \
  || as_root virsh --connect qemu:///system undefine "$BUILDER_VM_NAME" >/dev/null
as_root qemu-img check "$BUILDER_DISK"
as_root mv "$BUILDER_DISK" "$FINAL_IMAGE"
as_root chown root:root "$FINAL_IMAGE"
as_root chmod 0444 "$FINAL_IMAGE"
command -v setfacl >/dev/null 2>&1 && as_root setfacl -m u:libvirt-qemu:r-- "$FINAL_IMAGE"
as_root rm -rf "$BUILD_DIR"

printf '\nGolden image created: %s\n' "$FINAL_IMAGE"
as_root qemu-img info "$FINAL_IMAGE"
