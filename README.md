# Drives for Omarchy

A bar widget and panel for your disks on Omarchy Quattro.

- See every disk: how full it is, its health and live read/write speed.
- Set up a new data drive, encrypted, that unlocks together with your OS.
- Move a big folder (say `~/Videos`) onto that drive. Apps keep using
  `~/Videos` as before; the files just live on the other drive.
- Find out what fills your OS disk, and move it from there.

## Install

One line:

```sh
omarchy plugin add https://github.com/Zeus-Deus/drives-omarchy-plugin.git --enable --yes && sudo bash ~/.config/omarchy/plugins/io.github.zeus-deus.drives/helper/install.sh
```

The first part adds **Drives** to your bar. The second installs a small
system helper that does the disk work (setting up drives, moving folders).
Without the helper the panel only shows your disks. Everything it needs is
already part of Omarchy.

Disk health needs `smartmontools`. The panel offers to install it.

Update:

```sh
omarchy plugin update io.github.zeus-deus.drives && sudo bash ~/.config/omarchy/plugins/io.github.zeus-deus.drives/helper/install.sh && omarchy-restart-shell
```

## Uninstall

One line:

```sh
sudo bash ~/.config/omarchy/plugins/io.github.zeus-deus.drives/helper/uninstall.sh && omarchy plugin remove io.github.zeus-deus.drives --yes
```

Your drives and moved folders keep working without the plugin. Uninstall
refuses while a move is waiting for a restart.

## Moving a folder

1. Open **Drives**, choose **What's using it** on your OS disk, and pick a
   folder (or type one).
2. Review the move and enter your password. Nothing moves yet.
3. Restart. This restart takes a little longer: the folder is copied, every
   file is checked, then your desktop comes back. Don't turn the computer off
   during it.

You don't need to close apps first; the restart closes them cleanly. If the
power goes out halfway, your original folder comes back unchanged.

## Getting the space back

After a move, **the old copy stays on your OS disk** as a safety net. It is
not deleted automatically, so the space is not freed yet.

When you're happy with the move:

1. Open **Drives** and click the folder under **Moved folders**.
2. Choose **Delete old copy** and confirm.

The panel shows how far the deletion is. Big folders take a few minutes. If
you restart before it finishes, Drives finishes it by itself afterwards.

On Btrfs, snapshots can keep the deleted data for a while, so the free space
may show up a bit later.

## Changed your mind?

Click the moved folder:

- **Undo move** puts the original back exactly as it was. Only while the old
  copy still exists, and only if nothing changed since the move.
- **Move back** copies your latest files back to the OS disk during a
  restart, including changes since the move. Works even after you deleted
  the old copy.

## Good to know

- Every change asks for your password. The only thing Drives does on its own
  is finishing a deletion you already confirmed.
- New drives get a recovery passphrase, for a reinstall or another computer.
  It is typed in a separate small window, never in the panel.
- A drive that unlocks with the OS keeps its key on the OS disk, so it is
  only as safe as your OS disk encryption. Drives refuses this on an
  unencrypted OS disk.
- Export the drive's header backup (on the drive's page) and keep it off this
  computer.
- Drives and folders you set up by hand are shown, never changed. The first
  move onto such a drive makes only its top folder (like `/data`)
  system-owned; everything inside stays yours. The review tells you first.
- It won't move your whole home folder, folders outside your home, a folder
  with another drive mounted inside it, or files hard-linked from elsewhere.

More detail: [docs/DESIGN.md](docs/DESIGN.md). For contributors:
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

License: MIT.
