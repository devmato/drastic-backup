#!/usr/bin/env bash
# Required container packages and optional Proxmox host packages.
set -u

if [ "${DRASTIC_AGENT_DEPLOYMENT:-}" = docker ]; then
    # Required packages are shared by the image build and Git-based container updates.
    export DEBIAN_FRONTEND=noninteractive
    apt-get -o DPkg::Lock::Timeout=60 update &&
        apt-get -o DPkg::Lock::Timeout=60 install -y --no-remove --no-install-recommends \
            ca-certificates git openssh-client
    exit $?
fi

command -v pveversion >/dev/null 2>&1 || exit 0
PACKAGES=(python3-guestfs libguestfs-tools python3-fuse python3-libnbd libnbd-bin)
MISSING=false
for package in "${PACKAGES[@]}"; do
    [ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null)" = 'install ok installed' ] || MISSING=true
done
if [ "$MISSING" = false ] && /usr/bin/python3 -c 'import guestfs, fuse, nbd' >/dev/null 2>&1; then
    exit 0
fi

skip() {
    printf '%s\n' "Optional Proxmox dependencies unavailable: $*. Retry an agent update as root for native backups and file restore." >&2
    exit 0
}

command -v apt-get >/dev/null 2>&1 || skip 'apt-get is missing'
PREFIX=()
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || skip 'root or sudo is required'
    (: </dev/tty) 2>/dev/null || skip 'sudo requires an interactive terminal'
    sudo -v </dev/tty || skip 'sudo authorization was declined or failed'
    PREFIX=(sudo -n)
fi

printf '%s\n' 'Installing optional Proxmox native backup and file restore dependencies...'
"${PREFIX[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=60 update &&
    "${PREFIX[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=60 install \
        -y --no-remove --no-install-recommends "${PACKAGES[@]}" || skip 'package installation failed'
/usr/bin/python3 -c 'import guestfs, fuse, nbd' >/dev/null 2>&1 || skip 'the system Python cannot import guestfs, fuse or nbd'
printf '%s\n' 'Proxmox native backup and file restore dependencies are available.'
