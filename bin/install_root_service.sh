#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root (via pkexec)." >&2
  exit 1
fi

if python3 - <<'PY' >/dev/null 2>&1
import cups
PY
then
  echo "pycups already installed; nothing to do."
  exit 0
fi

if [[ -r /etc/os-release ]]; then
  # shellcheck source=/dev/null
  source /etc/os-release
fi

os_id="${ID:-}"
os_like="${ID_LIKE:-}"

install_debian() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3-cups
}

install_arch() {
  pacman -Sy --noconfirm python-pycups
}

if command -v apt-get >/dev/null 2>&1 && {
  [[ "$os_id" == "debian" || "$os_id" == "ubuntu" ]] || [[ "$os_like" == *"debian"* ]];
}; then
  install_debian
elif command -v pacman >/dev/null 2>&1 && {
  [[ "$os_id" == "arch" ]] || [[ "$os_like" == *"arch"* ]];
}; then
  install_arch
elif command -v apt-get >/dev/null 2>&1; then
  install_debian
elif command -v pacman >/dev/null 2>&1; then
  install_arch
else
  echo "Unsupported distro/package manager. Install manually: Debian/Ubuntu -> python3-cups, Arch -> python-pycups" >&2
  exit 2
fi

if python3 - <<'PY' >/dev/null 2>&1
import cups
PY
then
  echo "pycups installed successfully."
  exit 0
fi

echo "Package install finished but python3 still cannot import cups." >&2
exit 3
