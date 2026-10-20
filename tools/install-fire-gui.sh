#!/usr/bin/env bash
# Put "beacon fire" in the app menu for the current user. No sudo.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
dest=~/.local/share/applications
mkdir -p "$dest"
install -m644 "$here/beacon-fire.desktop" "$dest/beacon-fire.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$dest" || true
echo "installed $dest/beacon-fire.desktop (launch: gtk-launch beacon-fire, or the app menu)"
