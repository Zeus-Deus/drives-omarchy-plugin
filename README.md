# Drives for Omarchy

A bar widget and panel for encrypted data drives on Omarchy Quattro.

- See every disk, how full it is, its health (SMART) and live read/write.
- Set up a new drive: LUKS2 encryption, unlocks with the OS, plus a recovery
  passphrase for a reinstall or new machine.
- Move a folder such as `~/Videos` onto the drive while keeping its path. Apps
  keep using `~/Videos`; the files live on the encrypted drive.
- Undo a move, or delete the old copy once you are happy.
- Recover after a reinstall (recovery passphrase) or when a drive was
  unplugged (reconnect).

## How moves work

A folder is never moved while you are using it. You plan the move in the
panel, then restart. During that restart nothing else runs: the folder is
copied, every file is checked, and the old path then opens the drive. The
computer restarts once more into your normal desktop. If power is cut halfway,
the next start puts your original folder back unchanged.

The original is kept until you choose **Delete old copy**, which is only
offered after one normal restart with the move working. **Undo** is refused if
anything changed since the move, so it never loses new work.

What will not move: browser and agent profiles, keyrings, `.ssh`/`.gnupg`,
databases, unreadable files, open files, and files hardlinked from outside the
folder. The panel says why instead of skipping anything.

## Install

1. Add the plugin: `omarchy plugin add <repo> --yes --enable`, then put
   **Drives** on your bar in Setup → Plugins.
2. Without the helper the panel is a read-only overview. To set up drives and
   move folders, install the root helper explicitly:

   ```sh
   sudo bash ~/.config/omarchy/plugins/io.github.zeus-deus.drives/helper/install.sh
   ```

   It needs `cryptsetup rsync btrfs-progs python-gobject gtk4`. Install
   `smartmontools` for drive health.

Every write action asks for your administrator password through Omarchy's
polkit prompt. The recovery passphrase is entered in a separate small window,
never in the panel.

## Uninstall

```sh
sudo bash ~/.config/omarchy/plugins/io.github.zeus-deus.drives/helper/uninstall.sh
omarchy plugin remove io.github.zeus-deus.drives
```

The uninstaller refuses while a move is scheduled. It keeps drive keys,
crypttab/fstab entries and your data, so moved folders keep working.

## Good to know

- Auto-unlock stores the drive key on the OS disk, so it is only as safe as
  your OS disk encryption. The panel refuses auto-unlock on an unencrypted OS
  disk.
- Export the LUKS header backup somewhere off this computer.
- udisks already lets your user format some removable disks without a
  password. This plugin does not widen that; its own actions all need admin
  authorization.
- On Btrfs, snapshots can keep deleted data, so deleting an old copy may not
  free space right away.

Design details: [docs/DESIGN.md](docs/DESIGN.md). Contributor notes and test
results: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

License: MIT.
