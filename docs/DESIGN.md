# Drives: design

Plugin ID `io.github.zeus-deus.drives`. A native Omarchy Quattro bar widget and
KeyboardPanel, plus a separate root storage helper. QML holds no privilege and
never sees a secret: no sudo, pkexec, polkit rule or password field in QML.

## Pieces

| Part | Runs as | Does |
|---|---|---|
| `Panel.qml`, `Service.qml`, `Model.js` | user, inside omarchy-shell | Overview, wizards, move rows. All decisions about wording and offered actions live in `Model.js` (Node-tested). |
| `bridge.py`, `topology.py` | user | One bounded JSON request → one response. Read-only topology from `/proc/1/mountinfo`, `lsblk`, `statvfs`, `/proc/diskstats`; writes go to the helper over D-Bus. |
| `helper/agent.py` | user | Separate GTK4 window for the recovery passphrase. Seals it in a memfd and passes the FD over D-Bus. |
| `helper/service.py` (`drives-helper.service`) | root, `ProtectSystem=strict` | System D-Bus helper. One polkit `auth_admin` action per write. Re-derives every precondition itself. |
| `helper/offline.py` (`drives-maintenance-worker.service`) | root, maintenance boot only | Performs folder moves and Undo while nothing else is running. |

## Status (read, no auth)

`Status()` returns moves, drives, jobs, `restartPending`, `health` and
`testFixtureMode`. The bridge adds disks (id, byId, model, serial, size, system,
encrypted, mounts, state, selectable, usage, `io` byte counters), mounts,
`rootEncrypted` and `sampledAt`. The panel polls once a second while open and
not at all while closed.

`health` is a SMART verdict per serial (`passed`, `warning`, `failing`,
`unavailable` with a reason). The helper runs `smartctl` in the background
with a 10-minute cache, so Status never waits on it. USB bridges and virtual
disks report `unavailable`, never a guessed "healthy".

## Writes (system D-Bus `io.github.zeus_deus.Drives`)

| Method | Polkit action | Notes |
|---|---|---|
| `ProvisionDrive(s request, h secretFd)` | `provision` | LUKS2 + keyfile + recovery keyslot, Btrfs on the mapper, crypttab/fstab, header backup. Typed serial fragment checked again in the helper. |
| `ResumeDrive(s id)` | `resume-drive` | Finish an interrupted setup; never formats again. |
| `UnlockDrive(s id, h secretFd)` | `unlock` | Recovery passphrase → opens under the configured mapper name, mounts the configured path, reconnects moved folders. |
| `ReconnectDrive(s id)` | `reconnect` | Drive plugged back in after boot: starts its own `systemd-cryptsetup@` unit (keyfile), mounts it, reconnects moved folders. |
| `StartMove(s src, s destMount)` | `move` | Plans only. Creates the move's private folder on the drive and journals `awaiting-maintenance`. |
| `ResumeMove(s id)` / `RollbackMove(s id)` | `resume` / `rollback` | Arm the next-boot request for Continue / Undo. |
| `CancelRestart(s id)` | `resume` | Withdraw this session's own request. |
| `CancelMove(s id)` / `RestartMove(s id)` | `cancel` / `restart` | Discard the planned destination (pre-switch only). |
| `DeleteOldCopy(s id)` | `delete` | Only after a verified move has survived one normal restart with its bind active. |
| `ExportHeaderBackup(s name, s destination)` | `export` | Copies the LUKS header backup to a path in the caller's home. |

Secrets only ever arrive as a sealed memfd (8–4096 bytes). Never in argv, the
environment, D-Bus strings or logs.

### Namespace rule

The helper never lists a drive mountpoint in `ReadWritePaths`. When a drive is
locked its automount cannot be bind-mounted into the namespace, and the helper
would fail to start (226/NAMESPACE) exactly when recovery is needed. Every
write to a drive runs in a fixed transient worker that can write only there:
`helper/destination.py` (create/discard a move's folder; re-checks the
recorded identity before deleting), `helper/mountproof.py`. Config edits go
through `helper/configwriter.py`, the root latch through `helper/latch.py`,
udev rules through `helper/udevhide.py`.

### Desktop automount

Omarchy autostarts `udiskie`, which would pop a generic "Enter password for
/dev/sdX1" dialog for a locked Drives volume and open it under the wrong name.
Every Drives volume gets `/etc/udev/rules.d/90-drives-<name>.rules` setting
`UDISKS_IGNORE=1` (on setup, on recovery unlock, and for existing drives when
the helper is reinstalled).

## Moving a folder (protocol 2)

1. **Plan (normal session).** Preflight refuses: profiles, credentials and
   databases (deny list), unreadable subtrees, open files (lists the
   processes, never kills them), FIFOs/sockets/devices, nested mounts, files
   hardlinked from outside the folder, a destination that is not one
   encrypted keyfile drive, and less than 1.2× the folder size free.
2. **Request.** "Move on next restart" writes the root-only latch
   `/drives-maintenance-request.json`. "Restart now" runs Omarchy's normal
   `omarchy-system-reboot`.
3. **Maintenance boot.** An early generator selects
   `drives-maintenance.target`: no desktop, no user sessions, no timers. A
   sysinit guard keeps a normal boot from starting while the latch exists.
4. **Worker.** Re-audits the boot (target active, masks, no sessions, jobs or
   unexpected processes), then: quarantine the original in a root-private
   store on the same filesystem → `rsync -aHAXS --numeric-ids` → full
   checksum + metadata + count verification → immutable empty placeholder at
   the old path → fstab bind (`bind,nofail,x-systemd.requires=<drive>`) →
   read/write proof → reboot.
5. **After.** The familiar path opens the drive. The original stays in
   quarantine. Undo and Delete old copy are offered from the panel.

**Undo** runs in the same kind of boot. It is refused if anything changed
after the move (rsync itemize; directory-timestamp-only changes ignored), so
new work is never lost.

**Interruptions.** An interrupted Continue puts the untouched original back
and pauses the move; it never resumes copying on its own. An interrupted Undo
finishes restoring. Anything ambiguous keeps both copies, marks the move
"Needs attention" and boots normally rather than stranding the user at a
blank screen. A request that cannot run (drive unplugged, audit refused)
changes nothing and is cleared.

Journal states: `planned`, `awaiting-maintenance`, `quarantining`, `copying`,
`verifying`, `switching`, `switched`, `rebooted`, `rolling-back`, `cleaning`,
`cleaned`, `rolled-back`, `paused`. Every state is fsync'd (file, rename,
directory) before acting. The maintenance log is
`/var/lib/drives-helper/maintenance.log`, because the maintenance boot's
journal is volatile.

## Missing, locked and re-plugged drives

The configured-drive row tells the user what to do:

- **Missing**: plug it back in. Moved folders stay mode-000 placeholders, so
  nothing is written to the OS disk.
- **Connected but not in use** (plugged in after boot, or binds down):
  *Reconnect drive* (keyfile, no passphrase).
- **Key missing on this computer** (reinstall, new machine): *Unlock with
  recovery passphrase…* opens the GTK agent.

## Not done (by design or not yet)

- Manual-unlock ("ask every time") drives cannot be move destinations: the
  maintenance boot has no prompt.
- TPM2 unlock is not offered.
- Folder sizes in the breakdown are the apparent size recorded at move time,
  not a live `btrfs filesystem du`.
