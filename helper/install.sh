#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID != 0 ]]; then printf '%s
' 'Run explicitly: sudo bash helper/install.sh'; exit 1; fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
for tool in python3 cryptsetup rsync btrfs busctl lsblk wipefs systemd-escape; do command -v "$tool" >/dev/null || { printf 'Missing dependency: %s
' "$tool"; exit 1; }; done
/usr/bin/python3 -c 'import gi; from gi.repository import Gio; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk'
for dir in /usr/lib/drives-helper /etc/cryptsetup-keys.d /mnt/drives /etc/dbus-1/system.d /etc/systemd/system /etc/systemd/system/sysinit.target.d /etc/systemd/system-generators /usr/share/polkit-1/actions; do
 [[ ! -L $dir ]] || { printf 'Refusing symlink: %s
' "$dir"; exit 1; }
 install -d -m 0755 "$dir"
done
if [[ ! -e /etc/crypttab ]]; then install -m 0600 /dev/null /etc/crypttab; fi
install -d -m 0755 /usr/lib/drives-helper/helper
install -m 0644 "$source_dir"/helper/*.py /usr/lib/drives-helper/helper/
install -m 0644 "$source_dir/topology.py" /usr/lib/drives-helper/
install -m 0644 "$source_dir"/packaging/drives-helper.service /etc/systemd/system/
install -m 0644 "$source_dir"/packaging/drives-maintenance.target "$source_dir"/packaging/drives-maintenance-splash.service "$source_dir"/packaging/drives-maintenance-audit.service /etc/systemd/system/
install -m 0644 "$source_dir"/packaging/drives-normal-boot-guard.service /etc/systemd/system/
install -m 0644 "$source_dir"/helper/normal_boot_guard.py /usr/lib/drives-helper/normal_boot_guard.py
install -m 0644 "$source_dir"/packaging/drives-sysinit-guard.conf /etc/systemd/system/sysinit.target.d/drives-normal-boot-guard.conf
install -m 0755 "$source_dir"/packaging/drives-maintenance-generator /etc/systemd/system-generators/
install -m 0644 "$source_dir/packaging/io.github.zeus-deus.drives.policy" /usr/share/polkit-1/actions/
install -m 0644 "$source_dir/packaging/io.github.zeus_deus.Drives.conf" /etc/dbus-1/system.d/
systemctl daemon-reload
systemctl reload dbus.service
systemctl enable --now drives-helper.service
systemctl restart drives-helper.service
printf '%s
' 'Helper installed. No disk was provisioned or folder moved.'
