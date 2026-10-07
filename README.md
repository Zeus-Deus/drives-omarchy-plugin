# Drives for Omarchy

A bar widget and panel for encrypted data drives on Omarchy Quattro.

- See every disk, how full it is, its health (SMART) and live read/write.
- Set up a new drive: LUKS2 encryption, unlocks with the OS, plus a recovery
  passphrase for a reinstall or new machine.
- Move a folder such as `~/Videos` onto the drive while keeping its path. Apps
  keep using `~/Videos`; the files live on the encrypted drive.
- Move the latest files back, undo an unchanged move, or delete the old copy.
- Recover after a reinstall (recovery passphrase) or when a drive was
  unplugged (reconnect).
- See what fills your OS disk, biggest folders first, and move one from there.
- Drives and bind mounts you set up yourself are recognised and shown (how
  the drive unlocks, which folders are bound from it), never changed.
- Apps: shows where Steam's game library and Docker's data live, and offers
  to move Steam's library onto a drive.

## How moves work

A folder is never moved while you are using it. Choose the folder and a data
drive in the panel. Drives checks it first, without an administrator prompt.
If an app or terminal is using it, it names the processes: close them or leave
the folder, then retry. Hidden folders and dormant app profiles are not
blocked just because of their names.

Review the move, then authorize **Schedule move** once. Any required drive
preparation is included in that confirmation; there is no separate command
or preparation step. The folder moves on your next restart; **Restart now**
is optional. During that restart nothing else runs: the folder is
copied, every file is checked, and the old path then opens the drive. The
computer restarts once more into your normal desktop. If power is cut halfway,
the next start puts your original folder back unchanged.

The original is kept until you choose **Delete old copy**, which is only
offered after one normal restart with the move working. **Undo** is refused if
anything changed since the move, so it never loses new work.

**Move back** copies the latest files to their original OS filesystem during a
restart, including changes and deletions since the move. It verifies everything
before removing the bind. It still works after **Delete old copy**; the SSD copy
and any retained original copy are kept, not automatically deleted. It requires
enough free space on the original filesystem and a plugin-owned private-layout
move. The confirmation defaults to Cancel.

If recovery cannot establish which complete copy is authoritative, normal
startup stays blocked for administrator recovery. An invalid request is not
permission to start applications against an absent or uncertain profile.

What will not move: a folder outside your own home, the whole home directory,
an existing or nested mount, a Btrfs subvolume root, unreadable files,
active files/mappings/programs, sockets/FIFOs/devices, and files hardlinked
from outside the folder. The panel says why instead of skipping anything.
Links inside a folder are preserved as links, without copying their targets.
Closed SQLite databases and app settings are assessed like other files;
this is not a promise of compatibility with every application's storage setup.

### Which directions are supported?

Choose among mounted encrypted data drives that unlock with the OS. This is
not yet a general any-drive-to-any-drive migration tool: an already
bind-mounted folder cannot be moved to another drive from the panel, and the
OS disk is not offered as a destination. **Undo** can restore an unchanged
moved folder to its original storage while the old copy is still kept; it is
not a migration of new or changed files back to that storage. **Move back** is
the separate latest-data return path for plugin-owned private-layout moves
originally on `/`, or a `/home` subvolume of the same OS filesystem. A separate
`/home` filesystem, handmade binds and older public-copy layouts are not adopted.
Untouched old plans must be cancelled and reassessed before further copying.

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

To move folders onto such a drive, select it normally. If its top folder
(for example `/data`) needs preparation, the move review explains and includes
it in the same authorization. Only that top folder becomes root-owned;
everything inside keeps its owner and permissions. This is permanent, even
if you later cancel the move. New top-level folders then require a move through
Drives or administrator access; existing user-owned folders remain writable.
Preparation preserves existing read/traverse access using an ACL rather than
making a private drive public. Each new move also uses a root-private wrapper
on the SSD, so a profile protected by your home directory is not exposed
through a second, public path on the drive.

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
