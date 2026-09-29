#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { printf '%s
' 'Run explicitly: sudo bash helper/uninstall.sh'; exit 1; }
systemctl disable --now drives-helper.service
rm -f /etc/systemd/system/drives-helper.service /usr/share/polkit-1/actions/io.github.zeus-deus.drives.policy /etc/dbus-1/system.d/io.github.zeus_deus.Drives.conf
systemctl daemon-reload
systemctl reload dbus.service
printf '%s
' 'Helper disabled and policies removed. Drive keys, journals, crypttab, fstab and all data are retained for safe recovery. Do not delete them while drives or binds are in use.'
