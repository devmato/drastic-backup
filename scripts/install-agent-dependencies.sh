#!/usr/bin/env bash
# Optional host packages: failure leaves the agent usable without guest file restore.
set -u

command -v pveversion >/dev/null 2>&1 || exit 0
PACKAGES=(python3-guestfs libguestfs-tools)
MISSING=false
for package in "${PACKAGES[@]}"; do
    [ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null)" = 'install ok installed' ] || MISSING=true
done
if [ "$MISSING" = false ] && /usr/bin/python3 -c 'import guestfs' >/dev/null 2>&1; then
    exit 0
fi

skip() {
    printf '%s\n' "Optional GuestFS dependencies unavailable: $*. Continuing without Proxmox file restore; retry an agent update as root." >&2
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

printf '%s\n' 'Installing optional Proxmox file restore dependencies...'
"${PREFIX[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=60 update &&
    "${PREFIX[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=60 install \
        -y --no-remove --no-install-recommends "${PACKAGES[@]}" || skip 'package installation failed'
/usr/bin/python3 -c 'import guestfs' >/dev/null 2>&1 || skip 'the system Python cannot import guestfs'
printf '%s\n' 'Proxmox file restore dependencies are available.'
