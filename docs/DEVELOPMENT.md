# Developing Drives

Read [DESIGN.md](DESIGN.md) first. This file covers how to test, the traps that
cost real time, and what has been verified.

## Where to test

Everything that touches disks, `/etc` or reboots runs in a disposable Omarchy
Quattro VM, never on a real machine. Test disks are qcow2 image files
hot-plugged as USB storage with serials starting `TEST`. `tests/vm_e2e.py`
refuses to run unless it is root inside a KVM guest.

The helper only relaxes its "auto-unlock needs an encrypted OS disk" rule when
the service has `DRIVES_VM_TESTING=1` (a root-owned drop-in), the disk serial
starts with `TEST`, and `systemd-detect-virt` says `kvm`. The panel then shows
a red "VM test fixture" note.

## Gates

```sh
node --test tests/*.test.cjs                                   # Model.js, Service.qml lifecycle
sudo env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
     -p no:cacheprovider --basetemp=/root/pt/<run> tests      # helper (in the VM, as root)
omarchy plugin validate .
/usr/lib/qt6/bin/qmllint -I <dir with qs -> /usr/share/omarchy/shell> -I /usr/lib/qt6/qml Panel.qml
```

Current results (VM, 2026-10-06): 154 Python passed as root; the 9 native GTK
window tests skip there and pass separately as the desktop user with
`DRIVES_GTK_TEST=1 WAYLAND_DISPLAY=wayland-1 python3 -m pytest
tests/test_agent_gtk.py` (9 passed). 48 Node passed, plugin validates.
qmllint has no errors; the remaining warnings are the kit's usual
`Style.font.*`/`Color.*` missing-property noise and the `onExited`
signal-parameter type.

The `test_service.py` sealed-memfd test needs a Python whose `fcntl` has
`F_SEAL_*` (the system Python has it; some uv builds do not).

## VM workflow

1. Pack the tree with `git ls-files -co --exclude-standard | tar czf src.tgz -T -`
   so new untracked modules ship and caches do not.
2. In the guest: extract, `sudo bash helper/install.sh`.
3. For the panel: copy into `~/.config/omarchy/plugins/io.github.zeus-deus.drives`
   and run `omarchy-restart-shell` (Panel.qml and Model.js changes need it).
4. Drive the panel through IPC: `qs ipc -p /usr/share/omarchy/shell call drives
   <open|status|goto|selectDisk|selectMove|moveAction|reconnect|...>`.
   `status` returns the snapshot, confirm-dialog state, rates, footer and
   bridge busy state.
5. Reboots: from outside the guest, use the VM's own QMP `system_reset`
   (keeps hot-plugged disks attached), or the panel's "Restart now" button. `sync` first. A move needs about 150 s for the
   maintenance boot plus its own reboot.
6. Polkit: write actions need an admin password. For unattended GUI tests a
   VM-only `/etc/polkit-1/rules.d/00-drives-qa.rules` returning YES for
   `io.github.zeus-deus.drives.*` and the test user is acceptable. First
   record that the panel shows "administrator authorization denied" without
   it, and remove it right after. Never ship it.

## Traps

- **Never put a drive mountpoint in the helper's `ReadWritePaths`.** A locked
  drive's automount makes the helper fail with 226/NAMESPACE, exactly when
  recovery unlock is needed. Use a transient worker unit instead.
- **Run the suite before arming a real request.** Several tests assert that the
  live root has no latch.
- **pytest basetemp must be root-owned.** The default is under a user-owned
  ancestor, which the root-private checks rightly refuse.
- **Path-remapped uninstall test.** Every absolute `/etc`, `/usr` or `/run`
  path in the rewritten uninstaller must be remapped, or the test deletes
  real files in the guest. The test asserts this.
- **Boot screen.** `plymouth quit` once hung, timed the splash unit out,
  failed the maintenance target, and the worker refused the move. Every
  splash step is bounded and fits inside the unit timeout.
- **udiskie** (autostarted by Omarchy) prompts for any locked LUKS partition.
  Drives volumes carry `UDISKS_IGNORE=1`; check for that rule if a password
  dialog ever appears for a Drives disk.
- **Undo and directory mtimes.** Creating and removing a probe file changes a
  directory's mtime; the divergence check ignores timestamp-only directory
  lines, and the read/write probe restores the mtime.

## Verified in the VM

| Scenario | Result |
|---|---|
| Provision a new drive; typed decoy serial | Refused. Correct serial: LUKS2 with 2 keyslots, keyfile 0400, crypttab/fstab lines match the existing drive, udev hide rule; survives a restart with no prompt. |
| Move hostile `~/Videos` (hardlinks, xattr, ACL, sparse, odd names, git worktree) | Checksum identical, all features intact, writable. |
| Move 60,006 files / 1.8 GB | 15 s copy + 33 s verify; checksum identical. Delete old copy freed 1.8 GB. |
| Move 80,000 small files | Checksum identical. |
| Panel buttons: Move on restart → confirm → Restart now | Moved; checksum identical. |
| Crash at copying, verifying, placeholder, fstab, quarantine-prepared | Original restored unchanged, move paused. |
| Crash during Undo | Undo finished on the next boot. |
| Undo after a new file was added | Refused; new file kept. |
| Open file, unreadable subtree, FIFO, `.ssh` inside, `~/.config`, outside hardlink, too little space | Each refused with a clear message. |
| Destination unplugged before the maintenance boot | "Drive not connected"; nothing changed; folder still usable. |
| Drive re-plugged after boot | Reconnect from the panel brings back the drive and its moved folder; checksum identical. |
| Keyfile removed (reinstall), restart | Helper starts, no stray password dialog, wrong passphrase refused, recovery passphrase restores the drive and both moved folders. |
| Hung boot screen | Previously cancelled a move; fixed and re-proven. |

## Not verified

- An encrypted OS disk. The VM's root is plain Btrfs, so the keyfile's
  protection by root encryption is untested here.
- NVMe or SATA disks. The test VM has no PCIe hotplug slots, so test disks are
  USB storage. SMART therefore reports "not available"; the passed/warning/
  failing paths are unit-tested only.
- Typing the admin password or the recovery passphrase into the real dialogs
  by hand. The recovery path was driven through the helper with the same
  memfd protocol, and the GTK window passes its native widget tests on the VM
  display, but nobody has typed into it end to end.
- Folders of hundreds of gigabytes, and a drive filling up mid-copy (the 1.2×
  free-space check runs before the copy).
- Manual-unlock drives as move destinations (refused by design).
