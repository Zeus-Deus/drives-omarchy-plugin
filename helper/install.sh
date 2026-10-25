#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { printf '%s\n' 'Run explicitly: sudo bash helper/install.sh'; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
for tool in python3 cryptsetup rsync btrfs busctl lsblk wipefs systemd-escape; do
 command -v "$tool" >/dev/null || { printf 'Missing dependency: %s\n' "$tool"; exit 1; }
done
/usr/bin/python3 -c 'import gi; from gi.repository import Gio; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk'
cd -- "$source_dir"
exec /usr/bin/python3 -B -m helper.installation install "$source_dir"
