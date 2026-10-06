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
- See what fills your OS disk, biggest folders first, and move one from there.
- Drives and bind mounts you set up yourself are recognised and shown (how
  the drive unlocks, which folders are bound from it), never changed.
- Apps: shows where Steam's game library and Docker's data live, and offers
  to move Steam's library onto a drive.

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

Two commands on Omarchy Quattro:

```sh
omarchy plugin add https://github.com/Zeus-Deus/drives-omarchy-plugin.git --enable --yes
sudo bash ~/.config/omarchy/plugins/io.github.zeus-deus.drives/helper/install.sh
```

The first adds **Drives** to your bar. On its own it is a read-only overview.
The second installs the small root helper that sets up drives and moves
folders; read it first if you like, it is short. Everything it needs
(`cryptsetup`, `rsync`, `btrfs-progs`, `python-gobject`, `gtk4`,
`smartmontools`) is already part of a standard Omarchy install.

Update later with `omarchy plugin update io.github.zeus-deus.drives` and
`omarchy-restart-shell`. When the helper changed too, run the same `install.sh`
again.

Every write action asks for your administrator password through Omarchy's
polkit prompt. The recovery passphrase is entered in a separate small window,
never in the panel.

## Uninstall

Two commands, in this order (the helper first, while its uninstaller is still
on disk):

```sh
sudo bash ~/.config/omarchy/plugins/io.github.zeus-deus.drives/helper/uninstall.sh
omarchy plugin remove io.github.zeus-deus.drives --yes
```

The uninstaller refuses while a move is scheduled. It keeps drive keys,
crypttab/fstab entries and your data, so drives still unlock at boot and moved
folders keep working without the plugin.

## Drives you set up yourself

A LUKS drive you already unlock at boot (crypttab keyfile) and mount in fstab
shows up as **unlocks with OS**, and the folders you bind-mounted from it are
listed. Drives never edits its crypttab or fstab lines.

To move folders onto such a drive, open it in the panel and choose **Prepare
for moves…** once. That makes only its top folder (for example `/data`) owned
by root, which the helper requires so no other program can swap a folder
mid-move. Everything inside stays yours and keeps working.

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
