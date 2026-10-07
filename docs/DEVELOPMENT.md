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

Latest installed Move back checkpoint (VM): **445 Python passed as root, 9
GTK tests skipped; 98 Node passed.** The skipped GTK source is unchanged from
the earlier separate native-widget run below. Installed tests proved latest
files returning after deletion of the original copy, preserving edits,
deletions, SQLite contents, modes, xattrs, hardlinks and literal symlinks.
Two queued cuts (copying → durable pause, and exchanged → durable completion)
recovered automatically: the pause did not replay and completion did not get
stuck behind its surviving request. The actual loaded review defaulted to
Cancel; administrator denial and active-file refusal changed no data. These
are storage/protocol/native-state results, not final visual sign-off.

Previous seamless-move checkpoint, before Move back (VM): 290 Python passed as root; the 9 native GTK
window tests skip there and pass separately as the desktop user with
`DRIVES_GTK_TEST=1 WAYLAND_DISPLAY=wayland-1 python3 -m pytest
tests/test_agent_gtk.py` (9 passed; the agent's source is unchanged since that
separate run). 91 Node passed, plugin validates.
qmllint has no errors; 40 warnings versus 36 baseline, including the dynamic
`bar.activePopout` member that the real shell provides. Other warnings are the kit's usual
`Style.font.*`/`Color.*` missing-property noise and the `onExited`
signal-parameter type.

### Wheel scrolling

Match the working PassPage wheel handler: one mouse notch moves three
two-line rows (`(Style.spacing.rowPaddingX + Style.space(38)) * 3`, normally
150 px), not three short popup rows (84 px). Cancel any kinetic flick before
stepping `contentY`, explicitly accept Mouse and TouchPad, and pass touchpad
pixel deltas through unchanged. Keep the pointer movement gate: a stationary
pointer must not take the cursor as rows scroll under it.

`tests/test_wheel_qml.py` extracts the actual panel WheelHandler and themed
step expression into Qt Quick Test, imports the real Model.js, and exercises
single/rapid/high-resolution notches, kinetic cancellation and both bounds.
It runs offscreen, without controlling the user's desktop. These tests and a
clean VM panel load do not establish physical-mouse feel on every device.

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
  splash step is bounded and fits inside the unit timeout. Bounded quit/wait
  plus TERM also left an initramfs daemon alive during a later recovery;
  the owned maintenance-only unit now escalates to KILL. Admission still
  independently rejects residual processes. No vendor normal-boot unit is
  changed or masked.
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
| Active fd/cwd/mmap, unreadable subtree, FIFO, nested mount, outside hardlink, too little space | Refused with a clear message; closed-app retry succeeds. Names such as `.config` or `.ssh` no longer decide admission. |
| Destination unplugged before the maintenance boot | "Drive not connected"; nothing changed; folder still usable. |
| Drive re-plugged after boot | Reconnect from the panel brings back the drive and its moved folder; checksum identical. |
| Keyfile removed (reinstall), restart | Helper starts, no stray password dialog, wrong passphrase refused, recovery passphrase restores the drive and both moved folders. |
| Hung boot screen | Previously cancelled a move; fixed and re-proven. |
| Native assessed move: Cancel-default review, denied administrator prompt, integrated preparation + scheduling | Cancel/denial changed nothing. One ScheduleMove prepared the TEST drive root and armed the next restart; no separate Prepare/Resume step. |
| Dormant SQLite/settings folder under `.config` | Restarted move, opened at its familiar path, exact hashes/modes/ACL/xattr/hardlinks/symlinks preserved. |
| Public-mode profile inside a private home, private destination wrapper | Other UID denied on the direct SSD path with a readable sibling control; owner could read the familiar bind path. |
| Preparing a private drive root | Original owner retained read/traverse access, other UID stayed denied, top-level writes blocked, existing child owners/modes unchanged. |
| Installed failure after preparation but before destination creation | Plan offered cancellation; Cancel + reassess succeeded without restarting the failed helper, and the original stayed unchanged. |
| Private-layout power cut at copying, then explicit Continue | Original restored unchanged and move paused; later restart completed, Delete old copy preserved live data. |
| Private-layout Undo | Original and metadata restored, wrapper removed. Two earlier profile reruns stalled in subsequent stock normal-boot Plymouth and required recovery resets. A later plain-folder follow-up completed Undo and its automatic normal reboot without a recovery reset; this does not establish a fix for the earlier intermittent stall. |
| Move back after edits/deletions and Delete old copy | Latest manifest matched locally; SQLite reopened, links/xattrs/owners/modes preserved, SSD retained. A cut after durable completion recovered its request. |
| Interrupted Move back, two cuts at copying and durable pause | SSD remained active, partial staging retained, no automatic new copy; a fresh request remained necessary. Latest installed rerun reached normal desktop without a recovery reset. |
| Return exchange followed by a cut after durable completion | Finished locally, owned bind removed, copies retained, latch cleared; normal desktop started automatically. |
| Returned source selected again | Ordinary caller assessment and a new move succeeded; retained destinations remained protected. |

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
- Universal third-party app-profile compatibility, arbitrary system/service
  storage moves, already-bound-folder retargeting, and OS-disk destinations.
- Final screenshot/three-theme comparison after the security hardening:
  screen capture was denied by the test-session policy. Earlier native flow
  screenshots and SSD-first IPC state are retained as separate evidence.
- The root cause of two earlier intermittent normal-startup stalls after
  private-layout profile Undo. Logs show `plymouth-start.service` timed out.
  A subsequent normal baseline reboot, move and plain-folder Undo boot passed
  without changing packaged Plymouth services. Keep earlier failures separate
  from those successful follow-ups; do not claim the intermittent failure fixed.
  A subsequent pre-escalation return-pause run also reached safe storage but
  stalled in stock Plymouth. Separately, completed-Undo recovery was correctly
  blocked by a surviving initramfs daemon. The maintenance-only KILL escalation
  addresses that verified residual-daemon failure; later double-cut runs passed,
  but do not claim that proves every earlier normal-startup stall fixed.

## Seamless-move maintenance rules

- Assess real ownership/use/storage conditions, not profile names. Keep
  source/ancestor symlink refusal, but preserve child symlinks literally;
  rsync must not copy their external targets. Preserve path bytes when a row
  selects a folder, including meaningful trailing spaces.
- Source permission copying must not remove effective ancestor privacy. New
  moves use a root-private mode-0700 wrapper and bind only its `content` child;
  record and validate both identities, including effective inherited ACL masks.
- Preparation must not make a private drive public. Preserve existing effective
  read/traverse grants and group identity, remove non-root writes, and freeze
  masked grants before adding the former owner to the access ACL.
- A scheduling failure is not a dead-end journal. Independently validate
  untouched pre-switch cancellation, never adopt/delete unknown destinations,
  retain permanent preparation, and release conflicts after cancellation.
- Read topology before probing a missing drive's private paths, so Status
  does not trigger an automount. Compare reboot-persistent parent identity
  using UUID/inode/subvolume, not raw `st_dev`.
- Keep negative offline fixtures' restoration/config writes stubbed too.
  An assertion raised inside the worker's try block enters its recovery path.
- Ambiguous recovery and invalid requests retain the persistent normal-startup
  barrier. Never rename the latch away or boot profile consumers on an uncertain
  source merely to avoid a blank maintenance screen.
- Legacy flat-copy plans cannot Continue into the unwrapped layout. Preserve
  inspection/Undo of completed legacy moves, but cancel and reassess untouched
  old plans rather than reintroducing direct SSD-path confidentiality loss.
- Move back copies current SSD content, not the frozen original. Stage privately
  on the original filesystem/subvolume, verify, exchange the owned placeholder
  atomically, and remove only the owned bind stanza. Retain all copies; once
  fstab removal is durably established, local edits are authoritative.
- Consume the validated action/armed-boot intent in the same durable journal
  write as a completed or safely paused outcome. A surviving latch dispatches
  proof-and-cleanup only, never a new copy. Permit the matching completed state
  through request validation; re-prove authoritative storage before clearing the
  startup barrier. Clear stale attention only after that proof succeeds.
- Test new Move back root components only inside the guest with
  `DRIVES_VM_TESTING=1`; nonprivileged components and mocked mounts are not
  production polkit/bind/reboot/crash qualification.
