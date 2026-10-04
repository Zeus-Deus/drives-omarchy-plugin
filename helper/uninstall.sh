#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { printf '%s
' 'Run explicitly: sudo bash helper/uninstall.sh'; exit 1; }
assert_uninstall_safe() {
 /usr/bin/python3 -B -c 'import os
for path in ("/drives-maintenance-request.json", "/run/drives-maintenance"):
 try: os.lstat(path)
 except FileNotFoundError: continue
 except OSError: raise SystemExit("Cannot inspect maintenance gate; refuse uninstall.")
 raise SystemExit("Refusing uninstall while maintenance recovery is armed; retain the gate and inspect as administrator.")'
 local state
 state=$(systemctl show drives-maintenance.target --property=ActiveState --value)
 [[ $state == inactive ]] || { printf '%s\n' 'Refusing uninstall while maintenance is active or its state is uncertain.'; exit 1; }
}
assert_uninstall_safe
systemctl stop drives-helper.service
# Stop the request producer, then recheck before removing recovery infrastructure.
assert_uninstall_safe
systemctl disable drives-helper.service
rm -f /etc/systemd/system/drives-helper.service /usr/share/polkit-1/actions/io.github.zeus-deus.drives.policy /etc/dbus-1/system.d/io.github.zeus_deus.Drives.conf
rm -f /etc/systemd/system/drives-maintenance.target /etc/systemd/system/drives-maintenance-splash.service /etc/systemd/system/drives-maintenance-audit.service /etc/systemd/system/drives-maintenance-worker.service /etc/systemd/system-generators/drives-maintenance-generator
# Remove the dependency without stopping its required unit: stopping the guard
# while sysinit requires it can tear down the running normal desktop.
rm -f /etc/systemd/system/sysinit.target.d/drives-normal-boot-guard.conf /etc/systemd/system/drives-normal-boot-guard.service /usr/lib/drives-helper/normal_boot_guard.py
systemctl daemon-reload
systemctl reload dbus.service
printf '%s
' 'Helper disabled and policies removed. Drive keys, journals, crypttab, fstab and all data are retained for safe recovery. Do not delete them while drives or binds are in use.'
